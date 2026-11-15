import hashlib
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import api, spatial_api, projects_api
from backend.app.api import CreateSessionRequest
from backend.app.db import Base
from backend.app.models import GameSessionRecord, MapPlacementRecord, PlayerRecord, ProjectRecord
from backend.app.projects_api import VersionRequest
from backend.app.spatial_api import (
    PlacementCreateRequest, PlacementDecisionRequest, decide_placement,
    get_map, propose_placement,
)


class SpatialPlacementApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )
        self.original_api_factory = api.SessionLocal
        self.original_spatial_factory = spatial_api.SessionLocal
        self.original_projects_factory = projects_api.SessionLocal
        api.SessionLocal = self.session_factory
        spatial_api.SessionLocal = self.session_factory
        projects_api.SessionLocal = self.session_factory

        created = api.create_session(CreateSessionRequest(scenario_id="ares-01"))
        self.session_id = created["id"]
        self.owner_id = created["owner_player_id"]
        self.owner_token = created["owner_token"]
        self.project_id = "test-construction-project"
        with self.session_factory.begin() as db:
            db.add(ProjectRecord(
                id=self.project_id, session_id=self.session_id,
                author_player_id=self.owner_id, title="Жилой модуль",
                description="Строительство жилого модуля.",
                project_type="construction", status="submitted",
                domain_codes=["life_support"], budget_credits=100,
                duration_periods=2, labor_fte=1, energy_kwh_per_period=1,
                resource_requirements={}, expected_result={}, version=1,
            ))
            self.approver_token = "independent-approver-token"
            db.add(PlayerRecord(
                session_id=self.session_id, display_name="Руководитель",
                token_hash=hashlib.sha256(self.approver_token.encode()).hexdigest(),
                roles=["domain_lead"],
                responsibility_domains=["construction", "life_support"],
            ))

    def tearDown(self):
        api.SessionLocal = self.original_api_factory
        spatial_api.SessionLocal = self.original_spatial_factory
        projects_api.SessionLocal = self.original_projects_factory
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_placement_requires_separate_profile_approvals(self):
        proposal = propose_placement(
            self.session_id,
            PlacementCreateRequest(
                project_id=self.project_id, name="Жилой модуль № 2",
                q=4, r=-2, expected_map_version=0,
            ),
            token=self.owner_token,
        )
        self.assertEqual(proposal["status"], "pending_approval")
        self.assertEqual(set(proposal["required_approvals"]), {"construction", "life_support"})

        first = decide_placement(
            self.session_id, proposal["id"],
            PlacementDecisionRequest(decision="approve", domain_code="construction"),
            token=self.approver_token,
        )
        self.assertEqual(first["status"], "pending_approval")
        second = decide_placement(
            self.session_id, proposal["id"],
            PlacementDecisionRequest(decision="approve", domain_code="life_support"),
            token=self.approver_token,
        )
        self.assertEqual(second["status"], "placed")
        self.assertEqual(second["map_version"], 1)

    def test_author_cannot_approve_own_placement(self):
        proposal = propose_placement(
            self.session_id,
            PlacementCreateRequest(project_id=self.project_id, name="Модуль", q=0, r=0),
            token=self.owner_token,
        )
        with self.assertRaises(HTTPException) as error:
            decide_placement(
                self.session_id, proposal["id"],
                PlacementDecisionRequest(decision="approve", domain_code="construction"),
                token=self.owner_token,
            )
        self.assertEqual(error.exception.status_code, 403)

    def test_occupied_pending_placement_cannot_be_overlapped(self):
        propose_placement(
            self.session_id,
            PlacementCreateRequest(project_id=self.project_id, name="Модуль A", q=2, r=3),
            token=self.owner_token,
        )
        with self.assertRaises(HTTPException) as error:
            propose_placement(
                self.session_id,
                PlacementCreateRequest(project_id=self.project_id, name="Модуль B", q=2, r=3),
                token=self.owner_token,
            )
        self.assertEqual(error.exception.status_code, 409)


    def test_construction_project_cannot_execute_without_placed_site(self):
        with self.session_factory.begin() as db:
            project = db.get(ProjectRecord, self.project_id)
            project.status = "approved"
        with self.assertRaises(HTTPException) as error:
            projects_api.execute_project(
                self.session_id, self.project_id, VersionRequest(expected_version=1),
                token=self.owner_token,
            )
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("согласовать", error.exception.detail)

    def test_map_exposes_axial_registry_and_legacy_assets(self):
        with self.session_factory.begin() as db:
            session = db.get(GameSessionRecord, self.session_id)
            state = dict(session.state)
            infrastructure = list(state.get("infrastructure", []))
            infrastructure.append({
                "id": "legacy-one", "name": "Старый объект",
                "kind": "habitat", "properties": {"x": 50, "y": 50},
            })
            state["infrastructure"] = infrastructure
            session.state = state

        result = get_map(self.session_id, token=self.owner_token)
        self.assertEqual(result["coordinate_system"], "axial")
        self.assertTrue(any(item["id"] == "legacy:legacy-one" for item in result["legacy_objects"]))


if __name__ == "__main__":
    unittest.main()

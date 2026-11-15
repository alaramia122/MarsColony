import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import api, projects_api
from backend.app.api import CreateSessionRequest
from backend.app.db import Base
from backend.app.projects_api import (
    ApprovalRequest, JoinRequest, ProjectCreate, RoleAssignment, VersionRequest,
)


class ProjectWorkflowTests(unittest.TestCase):
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
        self.original_session_factory = api.SessionLocal
        self.original_project_session_factory = projects_api.SessionLocal
        api.SessionLocal = self.session_factory
        projects_api.SessionLocal = self.session_factory
        self.session = api.create_session(CreateSessionRequest(scenario_id="ares-01"))
        self.session_id = self.session["id"]
        self.owner_token = self.session["owner_token"]
        self.original_loader = api.load_scenario

    def tearDown(self):
        api.SessionLocal = self.original_session_factory
        projects_api.SessionLocal = self.original_project_session_factory
        api.load_scenario = self.original_loader
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def join(self, name):
        return projects_api.join_session(
            self.session_id,
            JoinRequest(display_name=name, invite_code=self.session["join_code"]),
        )

    def assign(self, player_id, token, roles, domains=None):
        return projects_api.assign_roles(
            self.session_id, player_id,
            RoleAssignment(roles=roles, responsibility_domains=domains or []),
            token,
        )

    def test_full_project_approval_execution_and_audit_flow(self):
        domain_lead = self.join("Инженер энергетики")
        finance_lead = self.join("Финансовый координатор")
        self.assign(
            domain_lead["player_id"], self.owner_token,
            ["domain_lead"], ["energy"],
        )
        self.assign(
            finance_lead["player_id"], self.owner_token,
            ["finance_lead"],
        )

        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Резервная энергетическая линия",
                description="Установить резервную линию энергоснабжения.",
                project_type="infrastructure",
                domain_codes=["energy"],
                budget_credits=120,
                duration_periods=2,
                resource_requirements={"raw_ice": 50},
                expected_result={"availability_factor": 0.95},
            ),
            self.owner_token,
        )
        self.assertEqual(project["status"], "draft")

        submitted = projects_api.submit_project(
            self.session_id, project["id"], VersionRequest(expected_version=1),
            self.owner_token,
        )
        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(submitted["version"], 2)

        with self.assertRaises(HTTPException) as self_approval:
            projects_api.approve_project(
                self.session_id, project["id"],
                ApprovalRequest(decision="approve", expected_version=2),
                self.owner_token,
            )
        self.assertEqual(self_approval.exception.status_code, 403)

        domain_vote = projects_api.approve_project(
            self.session_id, project["id"],
            ApprovalRequest(decision="approve", expected_version=2),
            domain_lead["player_token"],
        )
        self.assertEqual(domain_vote["status"], "submitted")
        # A later role change must not invalidate an already recorded approval.
        self.assign(domain_lead["player_id"], self.owner_token, ["player"])
        finance_vote = projects_api.approve_project(
            self.session_id, project["id"],
            ApprovalRequest(decision="approve", expected_version=3),
            finance_lead["player_token"],
        )
        self.assertEqual(finance_vote["status"], "approved")
        self.assertEqual(finance_vote["version"], 4)

        executing = projects_api.execute_project(
            self.session_id, project["id"], VersionRequest(expected_version=4),
            self.owner_token,
        )
        self.assertEqual(executing["status"], "executing")
        self.assertEqual(executing["version"], 5)

        completed = projects_api.complete_project(
            self.session_id, project["id"], VersionRequest(expected_version=5),
            self.owner_token,
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["version"], 6)

        state = api.get_session(self.session_id, self.owner_token)
        self.assertEqual(state["state"]["budget_credits"], 480.0)
        event_types = [event["type"] for event in state["events"]]
        self.assertIn("project.submitted", event_types)
        self.assertIn("project.approval_recorded", event_types)
        self.assertIn("project.execution_started", event_types)
        self.assertIn("project.completed", event_types)
        sequences = [event["sequence"] for event in state["events"]]
        self.assertEqual(sequences, sorted(set(sequences)))

    def test_unassigned_player_cannot_create_project(self):
        player = self.join("Новый участник")
        with self.assertRaises(HTTPException) as error:
            projects_api.create_project(
                self.session_id,
                ProjectCreate(
                    title="Незаявленный проект",
                    description="Проверка отсутствия полномочий.",
                    project_type="research",
                    domain_codes=["science"],
                    budget_credits=0,
                    duration_periods=1,
                ),
                player["player_token"],
            )
        self.assertEqual(error.exception.status_code, 403)

    def test_invitation_and_player_credentials_are_session_scoped(self):
        player = self.join("Участник")
        with self.assertRaises(HTTPException) as bad_invite:
            projects_api.join_session(
                self.session_id,
                JoinRequest(display_name="Нарушитель", invite_code="x" * 30),
            )
        self.assertEqual(bad_invite.exception.status_code, 403)

        other_session = api.create_session(CreateSessionRequest(scenario_id="ares-01"))
        with self.assertRaises(HTTPException) as wrong_session:
            projects_api.list_players(self.session_id, other_session["owner_token"])
        self.assertEqual(wrong_session.exception.status_code, 401)
        self.assertTrue(player["player_token"])

    def test_rejected_project_releases_reserved_budget(self):
        domain_lead = self.join("Руководитель направления")
        self.assign(
            domain_lead["player_id"], self.owner_token,
            ["domain_lead"], ["energy"],
        )
        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Проект с отказом",
                description="Проверка освобождения бюджета.",
                project_type="repair",
                domain_codes=["energy"],
                budget_credits=200,
                duration_periods=1,
            ),
            self.owner_token,
        )
        projects_api.submit_project(
            self.session_id, project["id"], VersionRequest(expected_version=1),
            self.owner_token,
        )
        rejected = projects_api.approve_project(
            self.session_id, project["id"],
            ApprovalRequest(decision="reject", expected_version=2),
            domain_lead["player_token"],
        )
        self.assertEqual(rejected["status"], "rejected")
        with self.session_factory() as db:
            from backend.app.models import GameSessionRecord
            session = db.get(GameSessionRecord, self.session_id)
            self.assertEqual(session.reserved_budget_credits, 0.0)

    def test_stale_project_version_is_rejected(self):
        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Проект версии",
                description="Проверка optimistic concurrency.",
                project_type="research",
                domain_codes=["science"],
                budget_credits=0,
                duration_periods=1,
            ),
            self.owner_token,
        )
        projects_api.submit_project(
            self.session_id, project["id"], VersionRequest(expected_version=1),
            self.owner_token,
        )
        with self.assertRaises(HTTPException) as error:
            projects_api.submit_project(
                self.session_id, project["id"], VersionRequest(expected_version=1),
                self.owner_token,
            )
        self.assertEqual(error.exception.status_code, 409)

    def test_reserved_budget_prevents_competing_projects_from_overspending(self):
        project_ids = []
        for title in ("Первый проект", "Второй проект"):
            project = projects_api.create_project(
                self.session_id,
                ProjectCreate(
                    title=title,
                    description="Конкурирующая заявка на бюджет.",
                    project_type="infrastructure",
                    domain_codes=["energy"],
                    budget_credits=400,
                    duration_periods=1,
                ),
                self.owner_token,
            )
            project_ids.append(project["id"])

        projects_api.submit_project(
            self.session_id, project_ids[0], VersionRequest(expected_version=1),
            self.owner_token,
        )
        with self.assertRaises(HTTPException) as error:
            projects_api.submit_project(
                self.session_id, project_ids[1], VersionRequest(expected_version=1),
                self.owner_token,
            )
        self.assertEqual(error.exception.status_code, 409)
        with self.session_factory() as db:
            from backend.app.models import GameSessionRecord
            session = db.get(GameSessionRecord, self.session_id)
            self.assertEqual(session.reserved_budget_credits, 400.0)
            self.assertEqual(session.state["budget_credits"], 600.0)

    def test_cancelled_submitted_project_releases_budget(self):
        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Отменяемая заявка",
                description="Проверка возврата зарезервированных средств.",
                project_type="repair",
                domain_codes=["energy"],
                budget_credits=150,
                duration_periods=1,
            ),
            self.owner_token,
        )
        submitted = projects_api.submit_project(
            self.session_id, project["id"], VersionRequest(expected_version=1),
            self.owner_token,
        )
        cancelled = projects_api.cancel_project(
            self.session_id, project["id"],
            VersionRequest(expected_version=submitted["version"]),
            self.owner_token,
        )
        self.assertEqual(cancelled["status"], "cancelled")
        with self.session_factory() as db:
            from backend.app.models import GameSessionRecord
            session = db.get(GameSessionRecord, self.session_id)
            self.assertEqual(session.reserved_budget_credits, 0.0)

    def test_period_operating_cost_does_not_consume_reserved_project_budget(self):
        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Резерв на инфраструктуру",
                description="Проверка защиты резерва от операционных расходов.",
                project_type="infrastructure",
                domain_codes=["energy"],
                budget_credits=500,
                duration_periods=1,
            ),
            self.owner_token,
        )
        projects_api.submit_project(
            self.session_id, project["id"], VersionRequest(expected_version=1),
            self.owner_token,
        )
        api.advance_game_session(
            self.session_id,
            api.AdvancePeriodRequest(expected_period=0),
            player_token=self.owner_token,
        )
        restored = api.get_session(self.session_id, self.owner_token)
        self.assertEqual(restored["state"]["budget_credits"], 575.0)
        self.assertEqual(restored["reserved_budget_credits"], 500.0)
        self.assertEqual(restored["available_budget_credits"], 75.0)

    def test_last_commander_cannot_remove_own_authority(self):
        with self.assertRaises(HTTPException) as error:
            projects_api.assign_roles(
                self.session_id,
                self.session["owner_player_id"],
                RoleAssignment(roles=["player"]),
                self.owner_token,
            )
        self.assertEqual(error.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()

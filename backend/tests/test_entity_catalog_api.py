import base64
import unittest
from io import BytesIO

from PIL import Image

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import api, entity_catalog_api, projects_api
from backend.app.db import Base
from backend.app.api import CreateSessionRequest
from backend.app.entity_catalog_api import EntityApprovalRequest, EntityProposalRequest
from backend.app.projects_api import ProjectCreate


class EntityCatalogApiTests(unittest.TestCase):
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
        self.original_factories = (
            api.SessionLocal,
            projects_api.SessionLocal,
            entity_catalog_api.SessionLocal,
        )
        api.SessionLocal = self.session_factory
        projects_api.SessionLocal = self.session_factory
        entity_catalog_api.SessionLocal = self.session_factory
        self.session = api.create_session(CreateSessionRequest(scenario_id="ares-01"))
        self.session_id = self.session["id"]
        self.owner_token = self.session["owner_token"]
        self.player = projects_api.join_session(
            self.session_id,
            projects_api.JoinRequest(
                display_name="Руководитель энергетики",
                invite_code=self.session["join_code"],
            ),
        )
        projects_api.assign_roles(
            self.session_id,
            self.player["player_id"],
            projects_api.RoleAssignment(
                roles=["domain_lead"],
                responsibility_domains=["energy"],
            ),
            self.owner_token,
        )

    def tearDown(self):
        api.SessionLocal, projects_api.SessionLocal, entity_catalog_api.SessionLocal = self.original_factories
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_custom_entity_requires_independent_approval_before_catalogue_use(self):
        proposal = entity_catalog_api.propose_catalog_entity(
            self.session_id,
            EntityProposalRequest(
                entity_type="infrastructure",
                name="Компактный резервный генератор",
                description="Небольшой дополнительный источник электроэнергии.",
                domain_codes=["energy"],
                definition={
                    "kind": "custom_generator",
                    "build": {
                        "budget_credits": 50,
                        "duration_periods": 1,
                        "labor_fte": 0.5,
                        "energy_kwh_per_period": 1,
                        "resource_requirements": {},
                    },
                    "operation": {
                        "power_generation_kw": 2,
                        "power_load_kw": 0,
                        "resource_inputs_per_period": {},
                        "resource_outputs_per_period": {},
                        "operating_cost_credits_per_period": 0.1,
                    },
                },
                source_text="Проверка пользовательского определения.",
            ),
            self.player["player_token"],
        )
        self.assertEqual(proposal["status"], "pending_review")
        with self.assertRaises(HTTPException) as own_approval:
            entity_catalog_api.approve_catalog_entity(
                self.session_id, proposal["id"], self.player["player_token"]
            )
        self.assertEqual(own_approval.exception.status_code, 403)
        approved = entity_catalog_api.approve_catalog_entity(
            self.session_id, proposal["id"], self.owner_token
        )
        self.assertEqual(approved["status"], "approved")
        catalogue = entity_catalog_api.get_entity_catalog(self.session_id, self.owner_token)
        custom = next(
            item for item in catalogue["entities"]
            if item["id"] == proposal["catalog_id"]
        )
        self.assertEqual(custom["operation"]["power_generation_kw"], 2)
        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Строительство компактного генератора",
                description="Использовать утверждённый пользовательский тип из каталога.",
                project_type="infrastructure",
                domain_codes=["energy"],
                budget_credits=50,
                duration_periods=1,
                labor_fte=0.5,
                energy_kwh_per_period=1,
                catalog_entity_ids=[proposal["catalog_id"]],
            ),
            self.owner_token,
        )
        self.assertEqual(
            project["expected_result"]["infrastructure"][0]["catalog_entity_id"],
            proposal["catalog_id"],
        )

    def test_builtin_construction_catalogue_requires_real_material_stocks(self):
        request = ProjectCreate(
            title="Строительство солнечной электростанции",
            description="Проверка обязательных строительных материалов.",
            project_type="construction",
            domain_codes=["energy", "construction"],
            budget_credits=180,
            duration_periods=2,
            labor_fte=1.5,
            energy_kwh_per_period=12,
            catalog_entity_ids=["solar_array"],
        )
        with self.assertRaises(HTTPException) as missing_materials:
            projects_api.create_project(self.session_id, request, self.owner_token)
        self.assertEqual(missing_materials.exception.status_code, 422)
        request.resource_requirements = {"steel": 80, "aluminium": 60, "electronics": 15}
        project = projects_api.create_project(self.session_id, request, self.owner_token)
        self.assertEqual(
            project["resource_requirements"],
            {"steel": 80, "aluminium": 60, "electronics": 15},
        )

    def test_custom_entity_photo_is_sanitized_persisted_and_inherited_by_project_asset(self):
        image_buffer = BytesIO()
        Image.new("RGB", (2, 2), (180, 80, 40)).save(image_buffer, format="PNG")
        tiny_png = "data:image/png;base64," + base64.b64encode(image_buffer.getvalue()).decode("ascii")
        proposal = entity_catalog_api.propose_catalog_entity(
            self.session_id,
            EntityProposalRequest(
                entity_type="infrastructure",
                name="Фотогенератор",
                description="Проверка сохранения изображения объекта.",
                domain_codes=["energy"],
                definition={
                    "kind": "photo_generator",
                    "build": {"budget_credits": 5, "duration_periods": 1, "labor_fte": 0, "energy_kwh_per_period": 0, "resource_requirements": {}},
                    "operation": {"power_generation_kw": 1, "power_load_kw": 0, "resource_inputs_per_period": {}, "resource_outputs_per_period": {}, "operating_cost_credits_per_period": 0},
                },
                image_data_url=tiny_png,
            ),
            self.player["player_token"],
        )
        self.assertTrue(proposal["image_data_url"].startswith("data:image/webp;base64,"))
        image_catalog = entity_catalog_api.get_entity_catalog(self.session_id, self.owner_token)
        pending = next(item for item in image_catalog["pending_proposals"] if item["id"] == proposal["id"])
        self.assertTrue(pending["image_data_url"].startswith("data:image/webp;base64,"))
        entity_catalog_api.approve_catalog_entity(self.session_id, proposal["id"], self.owner_token)
        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Установка фотогенератора",
                description="Проверка передачи изображения из каталога в проект.",
                project_type="infrastructure",
                domain_codes=["energy"],
                budget_credits=5,
                duration_periods=1,
                labor_fte=0,
                energy_kwh_per_period=0,
                catalog_entity_ids=[proposal["catalog_id"]],
            ),
            self.owner_token,
        )
        asset = project["expected_result"]["infrastructure"][0]
        self.assertTrue(asset["properties"]["image_data_url"].startswith("data:image/webp;base64,"))

    def test_entity_photo_rejects_svg_and_invalid_payloads(self):
        with self.assertRaises(HTTPException) as error:
            entity_catalog_api.propose_catalog_entity(
                self.session_id,
                EntityProposalRequest(
                    entity_type="infrastructure",
                    name="Неподдерживаемый формат",
                    description="SVG не принимается как фотография.",
                    domain_codes=["energy"],
                    definition={"kind": "unsafe_photo_asset"},
                    image_data_url="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
                ),
                self.player["player_token"],
            )
        self.assertEqual(error.exception.status_code, 422)

    def test_commander_can_approve_own_definition_only_with_written_justification(self):
        proposal = entity_catalog_api.propose_catalog_entity(
            self.session_id,
            EntityProposalRequest(
                entity_type="infrastructure",
                name="Резервный энергомодуль",
                description="Дополнительная резервная генерация.",
                domain_codes=["energy"],
                definition={
                    "kind": "custom_backup_generator",
                    "build": {"budget_credits": 20, "duration_periods": 1, "labor_fte": 0.2, "energy_kwh_per_period": 0.1, "resource_requirements": {}},
                    "operation": {"power_generation_kw": 1, "power_load_kw": 0, "resource_inputs_per_period": {}, "resource_outputs_per_period": {}, "operating_cost_credits_per_period": 0},
                },
            ),
            self.owner_token,
        )
        with self.assertRaises(HTTPException) as missing_reason:
            entity_catalog_api.approve_catalog_entity(
                self.session_id, proposal["id"], self.owner_token
            )
        self.assertEqual(missing_reason.exception.status_code, 422)
        approved = entity_catalog_api.approve_catalog_entity(
            self.session_id,
            proposal["id"],
            self.owner_token,
            EntityApprovalRequest(comment="Резервный энергомодуль необходим для повышения устойчивости энергоснабжения."),
        )
        self.assertEqual(approved["status"], "approved")

    def test_player_cannot_propose_definition_outside_assigned_domains(self):
        with self.assertRaises(HTTPException) as unauthorized:
            entity_catalog_api.propose_catalog_entity(
                self.session_id,
                EntityProposalRequest(
                    entity_type="infrastructure",
                    name="Новый медицинский модуль",
                    description="Проверка границ полномочий.",
                    domain_codes=["medicine"],
                    definition={"kind": "medical_module"},
                ),
                self.player["player_token"],
            )
        self.assertEqual(unauthorized.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()

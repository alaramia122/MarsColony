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
        project_author = self.join("Инженер проекта")
        self.assign(
            domain_lead["player_id"], self.owner_token,
            ["domain_lead"], ["energy"],
        )
        self.assign(
            finance_lead["player_id"], self.owner_token,
            ["finance_lead"],
        )
        self.assign(
            project_author["player_id"], self.owner_token,
            ["engineer"], ["energy"],
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
                labor_fte=1,
                energy_kwh_per_period=100,
                resource_requirements={"raw_ice": 50},
                expected_result={
                    "resource_outputs_per_period": {"oxygen": 10},
                    "power_generation_kw_delta": 2,
                    "power_load_kw_delta": 0.5,
                    "infrastructure": [
                        {"name": "Резервная энергетическая линия", "kind": "power_unit"}
                    ],
                },
            ),
            project_author["player_token"],
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
                project_author["player_token"],
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

        with self.assertRaises(HTTPException) as early_completion:
            projects_api.complete_project(
                self.session_id, project["id"], VersionRequest(expected_version=5),
                self.owner_token,
            )
        self.assertEqual(early_completion.exception.status_code, 409)

        period_reports = []
        for expected_period in (0, 1, 2):
            period_result = api.advance_game_session(
                self.session_id,
                api.AdvancePeriodRequest(expected_period=expected_period),
                player_token=self.owner_token,
            )
            period_reports.append(period_result["report"])
        with self.session_factory() as db:
            from backend.app.models import ProjectRecord
            progressing = db.get(ProjectRecord, project["id"])
            self.assertEqual(progressing.progress_periods, 2)
            self.assertEqual(progressing.version, 7)

        completed = projects_api.complete_project(
            self.session_id, project["id"], VersionRequest(expected_version=7),
            self.owner_token,
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["version"], 8)

        state = api.get_session(self.session_id, self.owner_token)
        self.assertAlmostEqual(
            state["state"]["budget_credits"],
            600.0 - 120.0 - sum(report["budget_cost_credits"] for report in period_reports),
            places=5,
        )
        self.assertEqual(state["state"]["production_bonuses_per_period"]["oxygen"], 10.0)
        self.assertEqual(state["state"]["power_generation_kw_delta"], 2.0)
        self.assertEqual(state["state"]["power_load_kw_delta"], 0.5)
        commissioned_asset = next(
            asset for asset in state["state"]["infrastructure"]
            if asset["name"] == "Резервная энергетическая линия"
        )
        self.assertEqual(commissioned_asset["commissioned_period"], 3)
        event_types = [event["type"] for event in state["events"]]
        self.assertIn("project.submitted", event_types)
        self.assertIn("project.approval_recorded", event_types)
        self.assertIn("project.materials_consumed", event_types)
        self.assertIn("project.execution_started", event_types)
        self.assertIn("project.progressed", event_types)
        self.assertIn("project.paused_power_shortfall", event_types)
        self.assertIn("project.result_applied", event_types)
        self.assertIn("project.infrastructure_commissioned", event_types)
        self.assertIn("project.completed", event_types)
        sequences = [event["sequence"] for event in state["events"]]
        self.assertEqual(sequences, sorted(set(sequences)))

        final_period = api.advance_game_session(
            self.session_id,
            api.AdvancePeriodRequest(expected_period=3),
            player_token=self.owner_token,
        )
        oxygen_bonus = final_period["report"]["project_production_bonuses"]["oxygen"]
        self.assertEqual(oxygen_bonus["requested"], 10.0)
        self.assertEqual(oxygen_bonus["produced"], 10.0)
        self.assertEqual(final_period["report"]["commissioned_power_fraction"], 1.0)

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
        period_result = api.advance_game_session(
            self.session_id,
            api.AdvancePeriodRequest(expected_period=0),
            player_token=self.owner_token,
        )
        restored = api.get_session(self.session_id, self.owner_token)
        self.assertAlmostEqual(
            restored["state"]["budget_credits"],
            600.0 - period_result["report"]["budget_cost_credits"],
            places=5,
        )
        self.assertEqual(restored["reserved_budget_credits"], 500.0)
        self.assertAlmostEqual(
            restored["available_budget_credits"],
            restored["state"]["budget_credits"] - 500.0,
            places=5,
        )

    def test_last_commander_cannot_remove_own_authority(self):
        with self.assertRaises(HTTPException) as error:
            projects_api.assign_roles(
                self.session_id,
                self.session["owner_player_id"],
                RoleAssignment(roles=["player"]),
                self.owner_token,
            )
        self.assertEqual(error.exception.status_code, 409)

    def test_project_materials_are_consumed_at_execution_start(self):
        lead = self.join("Руководитель добычи")
        self.assign(lead["player_id"], self.owner_token, ["domain_lead"], ["geology"])
        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Пробная выработка",
                description="Проверка списания сырья при запуске.",
                project_type="production",
                domain_codes=["geology"],
                budget_credits=10,
                duration_periods=1,
                resource_requirements={"raw_ice": 125},
            ),
            lead["player_token"],
        )
        projects_api.submit_project(
            self.session_id, project["id"], VersionRequest(expected_version=1),
            lead["player_token"],
        )
        approved = projects_api.approve_project(
            self.session_id, project["id"],
            ApprovalRequest(decision="approve", expected_version=2),
            self.owner_token,
        )
        self.assertEqual(approved["status"], "approved")
        projects_api.execute_project(
            self.session_id, project["id"], VersionRequest(expected_version=3),
            self.owner_token,
        )
        state = api.get_session(self.session_id, self.owner_token)
        self.assertEqual(state["state"]["raw_ice_kg"], 6875.0)

    def test_project_cannot_start_without_required_materials(self):
        lead = self.join("Руководитель добычи")
        self.assign(lead["player_id"], self.owner_token, ["domain_lead"], ["geology"])
        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Невыполнимая выработка",
                description="Потребность превышает складской запас.",
                project_type="production",
                domain_codes=["geology"],
                budget_credits=10,
                duration_periods=1,
                resource_requirements={"raw_ice": 9000},
            ),
            lead["player_token"],
        )
        projects_api.submit_project(
            self.session_id, project["id"], VersionRequest(expected_version=1),
            lead["player_token"],
        )
        projects_api.approve_project(
            self.session_id, project["id"],
            ApprovalRequest(decision="approve", expected_version=2),
            self.owner_token,
        )
        with self.assertRaises(HTTPException) as error:
            projects_api.execute_project(
                self.session_id, project["id"], VersionRequest(expected_version=3),
                self.owner_token,
            )
        self.assertEqual(error.exception.status_code, 409)
        state = api.get_session(self.session_id, self.owner_token)
        self.assertEqual(state["state"]["raw_ice_kg"], 7000.0)

    def test_project_progress_pauses_when_dispatch_cannot_supply_energy(self):
        lead = self.join("Руководитель энергетики")
        self.assign(lead["player_id"], self.owner_token, ["domain_lead"], ["energy"])
        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Энергоёмкое строительство",
                description="Проверка приостановки при недопоставке энергии.",
                project_type="construction",
                domain_codes=["energy"],
                budget_credits=10,
                duration_periods=1,
                energy_kwh_per_period=100000,
            ),
            lead["player_token"],
        )
        projects_api.submit_project(
            self.session_id, project["id"], VersionRequest(expected_version=1),
            lead["player_token"],
        )
        projects_api.approve_project(
            self.session_id, project["id"],
            ApprovalRequest(decision="approve", expected_version=2),
            self.owner_token,
        )
        projects_api.execute_project(
            self.session_id, project["id"], VersionRequest(expected_version=3),
            self.owner_token,
        )
        api.advance_game_session(
            self.session_id, api.AdvancePeriodRequest(expected_period=0),
            player_token=self.owner_token,
        )
        with self.session_factory() as db:
            from backend.app.models import ProjectRecord
            saved = db.get(ProjectRecord, project["id"])
            self.assertEqual(saved.progress_periods, 0)
            self.assertEqual(saved.status, "executing")
        state = api.get_session(self.session_id, self.owner_token)
        self.assertIn(
            "project.paused_power_shortfall",
            [event["type"] for event in state["events"]],
        )

    def test_active_projects_cannot_overallocate_colony_workforce(self):
        lead = self.join("Руководитель строительства")
        self.assign(lead["player_id"], self.owner_token, ["domain_lead"], ["construction"])
        first = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Первый трудоёмкий проект",
                description="Занимает весь доступный FTE.",
                project_type="construction",
                domain_codes=["construction"],
                budget_credits=0,
                duration_periods=1,
                labor_fte=16,
            ),
            lead["player_token"],
        )
        projects_api.submit_project(
            self.session_id, first["id"], VersionRequest(expected_version=1),
            lead["player_token"],
        )
        projects_api.approve_project(
            self.session_id, first["id"],
            ApprovalRequest(decision="approve", expected_version=2),
            self.owner_token,
        )
        projects_api.execute_project(
            self.session_id, first["id"], VersionRequest(expected_version=3),
            self.owner_token,
        )

        second = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Второй трудоёмкий проект",
                description="Не должен превысить доступную численность.",
                project_type="construction",
                domain_codes=["construction"],
                budget_credits=0,
                duration_periods=1,
                labor_fte=1,
            ),
            lead["player_token"],
        )
        projects_api.submit_project(
            self.session_id, second["id"], VersionRequest(expected_version=1),
            lead["player_token"],
        )
        projects_api.approve_project(
            self.session_id, second["id"],
            ApprovalRequest(decision="approve", expected_version=2),
            self.owner_token,
        )
        with self.assertRaises(HTTPException) as error:
            projects_api.execute_project(
                self.session_id, second["id"], VersionRequest(expected_version=3),
                self.owner_token,
            )
        self.assertEqual(error.exception.status_code, 409)

    def test_engineer_can_create_scoped_project_but_cannot_approve_it(self):
        engineer = self.join("Инженер колонии")
        self.assign(engineer["player_id"], self.owner_token, ["engineer"], ["energy"])
        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Инженерная заявка",
                description="Проверка инженерных полномочий.",
                project_type="repair",
                domain_codes=["energy"],
                budget_credits=0,
                duration_periods=1,
            ),
            engineer["player_token"],
        )
        submitted = projects_api.submit_project(
            self.session_id, project["id"], VersionRequest(expected_version=1),
            engineer["player_token"],
        )
        self.assertEqual(submitted["status"], "submitted")
        with self.assertRaises(HTTPException) as error:
            projects_api.approve_project(
                self.session_id, project["id"],
                ApprovalRequest(decision="approve", expected_version=2),
                engineer["player_token"],
            )
        self.assertEqual(error.exception.status_code, 403)

    def test_ready_project_releases_fte_and_energy_until_commissioned(self):
        lead = self.join("Руководитель энергетики")
        self.assign(lead["player_id"], self.owner_token, ["domain_lead"], ["energy"])
        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Короткий энергетический проект",
                description="После срока работы ресурсы освобождаются.",
                project_type="repair",
                domain_codes=["energy"],
                budget_credits=0,
                duration_periods=1,
                labor_fte=16,
                energy_kwh_per_period=100,
            ),
            lead["player_token"],
        )
        projects_api.submit_project(
            self.session_id, project["id"], VersionRequest(expected_version=1),
            lead["player_token"],
        )
        projects_api.approve_project(
            self.session_id, project["id"],
            ApprovalRequest(decision="approve", expected_version=2),
            self.owner_token,
        )
        projects_api.execute_project(
            self.session_id, project["id"], VersionRequest(expected_version=3),
            self.owner_token,
        )
        api.advance_game_session(
            self.session_id, api.AdvancePeriodRequest(expected_period=0),
            player_token=self.owner_token,
        )
        with self.session_factory() as db:
            from backend.app.models import ProjectRecord
            ready = db.get(ProjectRecord, project["id"])
            self.assertEqual(ready.progress_periods, 1)
            self.assertEqual(ready.version, 5)
        second_period = api.advance_game_session(
            self.session_id, api.AdvancePeriodRequest(expected_period=1),
            player_token=self.owner_token,
        )
        self.assertEqual(second_period["report"]["project_execution"], [])
        with self.session_factory() as db:
            from backend.app.models import ProjectRecord
            ready = db.get(ProjectRecord, project["id"])
            self.assertEqual(ready.progress_periods, 1)
            self.assertEqual(ready.version, 5)

    def test_project_result_rejects_malformed_resource_output_contract(self):
        with self.assertRaises(HTTPException) as error:
            projects_api.create_project(
                self.session_id,
                ProjectCreate(
                    title="Некорректный выпуск",
                    description="Структура результата должна быть типизированной.",
                    project_type="production",
                    domain_codes=["industry"],
                    budget_credits=0,
                    duration_periods=1,
                    expected_result={"resource_outputs_per_period": "oxygen"},
                ),
                self.owner_token,
            )
        self.assertEqual(error.exception.status_code, 422)

    def test_repair_project_restores_infrastructure_condition(self):
        from backend.app.models import GameSessionRecord
        with self.session_factory.begin() as db:
            session = db.get(GameSessionRecord, self.session_id)
            state = dict(session.state)
            state["infrastructure"] = [{
                "id": "facility-1",
                "name": "Основной энергетический модуль",
                "kind": "power_unit",
                "condition": 0.4,
                "properties": {},
                "domain_codes": ["energy"],
                "commissioned_period": 0,
                "project_id": "seed",
            }]
            session.state = state
        lead = self.join("Руководитель энергетики")
        self.assign(lead["player_id"], self.owner_token, ["domain_lead"], ["energy"])
        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Ремонт энергетического модуля",
                description="Восстановление технического состояния оборудования.",
                project_type="repair",
                domain_codes=["energy"],
                budget_credits=0,
                duration_periods=1,
                expected_result={
                    "infrastructure_repairs": [{
                        "infrastructure_id": "facility-1",
                        "condition_delta": 0.35,
                    }]
                },
            ),
            lead["player_token"],
        )
        projects_api.submit_project(
            self.session_id, project["id"], VersionRequest(expected_version=1),
            lead["player_token"],
        )
        projects_api.approve_project(
            self.session_id, project["id"],
            ApprovalRequest(decision="approve", expected_version=2),
            self.owner_token,
        )
        projects_api.execute_project(
            self.session_id, project["id"], VersionRequest(expected_version=3),
            self.owner_token,
        )
        api.advance_game_session(
            self.session_id, api.AdvancePeriodRequest(expected_period=0),
            player_token=self.owner_token,
        )
        self.assertEqual(
            project["expected_result"]["infrastructure_repairs"][0]["infrastructure_id"],
            "facility-1",
        )
        completed = projects_api.complete_project(
            self.session_id, project["id"], VersionRequest(expected_version=5),
            self.owner_token,
        )
        self.assertEqual(
            completed["expected_result"]["infrastructure_repairs"][0]["condition_delta"],
            0.35,
        )
        session_state = api.get_session(self.session_id, self.owner_token)
        commissioned = next(
            event for event in session_state["events"]
            if event["type"] == "project.result_applied"
            and event["payload"]["project_id"] == project["id"]
        )
        self.assertAlmostEqual(
            commissioned["payload"]["infrastructure_repairs"][0]["condition_after"],
            0.75,
        )
        state = session_state["state"]
        self.assertAlmostEqual(state["infrastructure"][0]["condition"], 0.75)

    def test_research_project_unlocks_technology_on_completion(self):
        lead = self.join("Руководитель науки")
        self.assign(lead["player_id"], self.owner_token, ["domain_lead"], ["science"])
        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Новая технология переработки",
                description="Завершить исследовательскую программу.",
                project_type="research",
                domain_codes=["science"],
                budget_credits=0,
                duration_periods=1,
                expected_result={
                    "technology_unlocks": [{
                        "id": "water-electrolysis-v2",
                        "name": "Улучшенный электролиз воды",
                        "description": "Повышенная эффективность водородного контура.",
                        "level": 2,
                    }]
                },
            ),
            lead["player_token"],
        )
        projects_api.submit_project(
            self.session_id, project["id"], VersionRequest(expected_version=1),
            lead["player_token"],
        )
        projects_api.approve_project(
            self.session_id, project["id"],
            ApprovalRequest(decision="approve", expected_version=2),
            self.owner_token,
        )
        projects_api.execute_project(
            self.session_id, project["id"], VersionRequest(expected_version=3),
            self.owner_token,
        )
        api.advance_game_session(
            self.session_id, api.AdvancePeriodRequest(expected_period=0),
            player_token=self.owner_token,
        )
        projects_api.complete_project(
            self.session_id, project["id"], VersionRequest(expected_version=5),
            self.owner_token,
        )
        technologies = api.get_session(self.session_id, self.owner_token)["state"]["technologies"]
        self.assertEqual(technologies[0]["id"], "water-electrolysis-v2")
        self.assertEqual(technologies[0]["level"], 2)

    def test_commander_can_issue_documented_command_order_for_own_project(self):
        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Резервный контур энергетики",
                description="Создание учебной резервной линии.",
                project_type="infrastructure",
                domain_codes=["energy"],
                budget_credits=25,
                duration_periods=1,
            ),
            self.owner_token,
        )
        submitted = projects_api.submit_project(
            self.session_id, project["id"], VersionRequest(expected_version=1),
            self.owner_token,
        )
        with self.assertRaises(HTTPException) as missing_reason:
            projects_api.approve_project(
                self.session_id, project["id"],
                ApprovalRequest(decision="approve", expected_version=submitted["version"]),
                self.owner_token,
            )
        self.assertEqual(missing_reason.exception.status_code, 422)

        approved = projects_api.approve_project(
            self.session_id, project["id"],
            ApprovalRequest(
                decision="approve",
                expected_version=submitted["version"],
                comment="Приоритетное решение командира для устойчивости энергоснабжения.",
            ),
            self.owner_token,
        )
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["approvals"][0]["approval_mode"], "commander_order")
        session = api.get_session(self.session_id, self.owner_token)
        command_event = next(
            event for event in session["events"]
            if event["type"] == "project.approval_recorded"
            and event["payload"]["project_id"] == project["id"]
        )
        self.assertEqual(command_event["payload"]["approval_mode"], "commander_order")

    def test_project_can_select_and_instantiate_a_catalogue_asset(self):
        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Дополнительная солнечная ферма",
                description="Развернуть типовой энергетический объект из каталога.",
                project_type="construction",
                domain_codes=["energy", "construction"],
                budget_credits=180,
                duration_periods=2,
                labor_fte=1.5,
                energy_kwh_per_period=12,
                catalog_entity_ids=["solar_array"],
            ),
            self.owner_token,
        )
        asset = project["expected_result"]["infrastructure"][0]
        self.assertEqual(asset["catalog_entity_id"], "solar_array")
        self.assertEqual(asset["kind"], "power_unit")
        self.assertEqual(asset["properties"]["economic_effects"]["power_generation_kw"], 18)

    def test_project_cannot_create_construction_materials_as_a_free_bonus(self):
        with self.assertRaises(HTTPException) as error:
            projects_api.create_project(
                self.session_id,
                ProjectCreate(
                    title="Бесплатная сталь",
                    description="Попытка обойти материальный баланс.",
                    project_type="production",
                    domain_codes=["industry"],
                    budget_credits=0,
                    duration_periods=1,
                    labor_fte=0,
                    energy_kwh_per_period=0,
                    expected_result={"resource_outputs_per_period": {"steel": 1000}},
                ),
                self.owner_token,
            )
        self.assertEqual(error.exception.status_code, 422)

    def test_project_cannot_inject_unapproved_infrastructure_economic_effects(self):
        with self.assertRaises(HTTPException) as error:
            projects_api.create_project(
                self.session_id,
                ProjectCreate(
                    title="Несогласованный генератор",
                    description="Попытка обойти каталог экономических характеристик.",
                    project_type="infrastructure",
                    domain_codes=["energy"],
                    budget_credits=0,
                    duration_periods=1,
                    expected_result={
                        "infrastructure": [{
                            "name": "Генератор",
                            "kind": "power_unit",
                            "properties": {
                                "condition": 1.0,
                                "economic_effects": {"power_generation_kw": 9999},
                            },
                        }]
                    },
                ),
                self.owner_token,
            )
        self.assertEqual(error.exception.status_code, 422)

    def test_project_can_unlock_a_catalogue_procedure(self):
        project = projects_api.create_project(
            self.session_id,
            ProjectCreate(
                title="Регламент аварийного водоснабжения",
                description="Разработать и утвердить порядок ограничения расхода воды.",
                project_type="research",
                domain_codes=["life_support", "safety", "administration"],
                budget_credits=25,
                duration_periods=1,
                labor_fte=0.2,
                energy_kwh_per_period=0.1,
                catalog_entity_ids=["emergency_water_contingency"],
            ),
            self.owner_token,
        )
        procedure = project["expected_result"]["procedure_unlocks"][0]
        self.assertEqual(procedure["id"], "emergency_water_contingency")
        self.assertIsNone(project["expected_result"].get("infrastructure"))

    def test_project_cannot_select_catalogue_asset_outside_its_domains(self):
        with self.assertRaises(HTTPException) as error:
            projects_api.create_project(
                self.session_id,
                ProjectCreate(
                    title="Неверная зона ответственности",
                    description="Попытка создать объект без включения всех его направлений.",
                    project_type="construction",
                    domain_codes=["energy"],
                    budget_credits=180,
                    duration_periods=2,
                    labor_fte=1.5,
                    energy_kwh_per_period=12,
                    catalog_entity_ids=["solar_array"],
                ),
                self.owner_token,
            )
        self.assertEqual(error.exception.status_code, 422)

    def test_session_enforces_twenty_player_capacity(self):
        for index in range(19):
            self.join(f"Участник {index + 1}")
        with self.assertRaises(HTTPException) as error:
            self.join("Двадцать первый участник")
        self.assertEqual(error.exception.status_code, 409)
        players = projects_api.list_players(self.session_id, self.owner_token)
        self.assertEqual(len(players["players"]), 20)


if __name__ == "__main__":
    unittest.main()

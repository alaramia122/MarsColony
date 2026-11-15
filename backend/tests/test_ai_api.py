import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app import ai_api, api, projects_api
from backend.app.api import CreateSessionRequest
from backend.app.ai_gateway import ProjectProposal
from backend.app.db import Base
from backend.app.ai_api import ClarificationRequest, EventGenerationRequest, ProposalRequest


class FakeProvider:
    def generate_json(self, system_prompt, payload):
        if "инженерный редактор реестра знаний" in system_prompt:
            return {
                "entity_key": "oxygen_catalytic_filter",
                "entity_type": "technology",
                "name": "Каталитический фильтр кислорода",
                "description": "Концепция каталитической очистки кислородного контура.",
                "domain_codes": ["life_support"],
                "definition": {"maturity": "concept", "inputs": ["contaminated_gas"], "outputs": ["cleaned_gas"]},
                "assumptions": ["Параметры эффективности не подтверждены."],
                "risks": ["Требуется инженерная проверка."],
            }
        if "Уточни существующее проектное предложение" in system_prompt:
            return {
                "title": "Резервная линия кислорода — уточнённая",
                "description": "Создать резервную линию производства кислорода.",
                "project_type": "infrastructure",
                "domain_codes": ["life_support"],
                "budget_credits": 130,
                "duration_periods": 2,
                "labor_fte": 1,
                "energy_kwh_per_period": 50,
                "resource_requirements": {"raw_ice": 10},
                "expected_result": {"resource_outputs_per_period": {"oxygen": 5}},
                "rationale": "Уточнённая смета.",
                "assumptions": ["Производительность является игровой оценкой."],
                "risks": ["Потребуется проверка доступной мощности."],
                "questions": [],
            }
        if "оперативную сводку" in system_prompt:
            return {
                "headline": "Колония работает штатно",
                "situation": "Сводка сформирована по данным текущей сессии.",
                "key_changes": ["Изменений в переданном контексте нет."],
                "risks": [],
                "decisions_needed": [],
                "next_period_focus": ["Контролировать запасы."],
            }
        return {
            "title": "Пылевая буря в районе базы",
            "category": "environment",
            "severity": "moderate",
            "likelihood": 0.35,
            "trigger": "Наблюдение за погодными условиями.",
            "narrative": "В районе базы усиливается пылевая буря.",
            "player_options": ["Укрыть внешнее оборудование.", "Продолжить работы с ограничениями."],
            "consequences": ["Возможны задержки внешних работ."],
            "assumptions": ["Событие является сценарным предложением."],
        }

    def propose_project(self, text, context):
        self.text = text
        self.context = context
        return ProjectProposal(
            title="Резервная линия кислорода",
            description="Создать резервную линию производства кислорода.",
            project_type="infrastructure",
            domain_codes=["life_support"],
            budget_credits=120,
            duration_periods=2,
            labor_fte=1,
            energy_kwh_per_period=50,
            resource_requirements={"raw_ice": 10},
            expected_result={"resource_outputs_per_period": {"oxygen": 5}},
            rationale="Повышение устойчивости жизнеобеспечения.",
            assumptions=["Производительность является игровой оценкой."],
            risks=["Потребуется проверка доступной мощности."],
            questions=["Какой резерв мощности доступен?"],
        )


class AIProposalAPITests(unittest.TestCase):
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
            api.SessionLocal, projects_api.SessionLocal, ai_api.SessionLocal
        )
        api.SessionLocal = self.session_factory
        projects_api.SessionLocal = self.session_factory
        ai_api.SessionLocal = self.session_factory
        self.session = api.create_session(CreateSessionRequest(scenario_id="ares-01"))
        self.session_id = self.session["id"]
        self.token = self.session["owner_token"]
        self.provider = FakeProvider()
        self.provider_patch = patch.object(ai_api, "configured_provider", return_value=self.provider)
        self.provider_patch.start()

    def tearDown(self):
        self.provider_patch.stop()
        api.SessionLocal, projects_api.SessionLocal, ai_api.SessionLocal = self.original_factories
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()



    def test_knowledge_entity_is_reviewed_and_versions_are_preserved(self):
        first = ai_api.create_ai_entity(
            self.session_id,
            ai_api.EntityGenerationRequest(text="Добавить каталитический фильтр кислородного контура."),
            token=self.token,
        )
        self.assertEqual(first["status"], "pending_review")
        approved_first = ai_api.approve_ai_entity(
            self.session_id, first["id"], token=self.token
        )
        self.assertEqual(approved_first["status"], "active")
        second = ai_api.create_ai_entity(
            self.session_id,
            ai_api.EntityGenerationRequest(text="Обновить описание каталитического фильтра."),
            token=self.token,
        )
        self.assertEqual(second["version"], 2)
        approved_second = ai_api.approve_ai_entity(
            self.session_id, second["id"], token=self.token
        )
        self.assertEqual(approved_second["status"], "active")
        entities = ai_api.list_ai_entities(self.session_id, token=self.token)["entities"]
        statuses = {item["id"]: item["status"] for item in entities}
        self.assertEqual(statuses[first["id"]], "superseded")
        self.assertEqual(statuses[second["id"]], "active")


    def test_ai_request_quota_limits_player_to_eight_calls_per_hour(self):
        for _ in range(8):
            ai_api.create_colony_briefing(self.session_id, token=self.token)
        with self.assertRaises(HTTPException) as error:
            ai_api.create_colony_briefing(self.session_id, token=self.token)
        self.assertEqual(error.exception.status_code, 429)

    def test_ai_briefing_is_grounded_in_session_context(self):
        result = ai_api.create_colony_briefing(self.session_id, token=self.token)
        self.assertEqual(result["period"], 0)
        self.assertEqual(result["briefing"]["headline"], "Колония работает штатно")

    def test_game_event_requires_commander_and_explicit_publication(self):
        proposal = ai_api.create_ai_event_proposal(
            self.session_id, EventGenerationRequest(focus="погода"), token=self.token
        )
        self.assertEqual(proposal["status"], "pending_review")
        published = ai_api.publish_ai_event(
            self.session_id, proposal["id"], token=self.token
        )
        self.assertEqual(published["status"], "published")
        self.assertIsNotNone(published["published_sequence"])
        events = ai_api.list_ai_event_proposals(self.session_id, token=self.token)
        self.assertEqual(events["events"][0]["status"], "published")
        with self.assertRaises(HTTPException) as error:
            ai_api.publish_ai_event(self.session_id, proposal["id"], token=self.token)
        self.assertEqual(error.exception.status_code, 409)

    def test_game_event_can_be_rejected_by_commander(self):
        proposal = ai_api.create_ai_event_proposal(
            self.session_id, EventGenerationRequest(), token=self.token
        )
        rejected = ai_api.reject_ai_event(
            self.session_id, proposal["id"], token=self.token
        )
        self.assertEqual(rejected["status"], "rejected")

    def test_natural_language_becomes_persisted_pending_proposal(self):
        result = ai_api.create_ai_proposal(
            self.session_id,
            ProposalRequest(text="Построить резервную линию производства кислорода."),
            token=self.token,
        )
        self.assertEqual(result["status"], "pending_review")
        self.assertEqual(result["proposal"]["duration_periods"], 2)
        self.assertEqual(self.provider.context["scenario_id"], "ares-01")
        proposals = ai_api.list_ai_proposals(self.session_id, token=self.token)
        self.assertEqual(len(proposals["proposals"]), 1)


    def test_project_clarification_creates_new_version_and_keeps_history(self):
        proposal = ai_api.create_ai_proposal(
            self.session_id,
            ProposalRequest(text="Создать резервную линию кислорода."),
            token=self.token,
        )
        revised = ai_api.clarify_ai_proposal(
            self.session_id, proposal["id"],
            ClarificationRequest(text="Увеличить бюджет до 130 кредитов."),
            token=self.token,
        )
        self.assertEqual(revised["version"], 2)
        self.assertEqual(revised["proposal"]["budget_credits"], 130)
        self.assertEqual(len(revised["revision_history"]), 1)
        self.assertEqual(revised["dialogue"][0]["text"], "Увеличить бюджет до 130 кредитов.")

    def test_proposal_is_only_converted_to_project_after_explicit_player_action(self):
        proposal = ai_api.create_ai_proposal(
            self.session_id,
            ProposalRequest(text="Создать резервную линию кислорода."),
            token=self.token,
        )
        with self.session_factory() as db:
            from backend.app.models import ProjectRecord
            self.assertEqual(db.query(ProjectRecord).count(), 0)
        result = ai_api.create_project_from_ai_proposal(
            self.session_id, proposal["id"], token=self.token
        )
        self.assertEqual(result["proposal"]["status"], "project_created")
        self.assertEqual(result["project"]["status"], "draft")
        with self.session_factory() as db:
            from backend.app.models import ProjectRecord
            self.assertEqual(db.query(ProjectRecord).count(), 1)

    def test_non_commander_without_domain_cannot_create_ai_proposal(self):
        from backend.app.projects_api import JoinRequest
        player = projects_api.join_session(
            self.session_id,
            JoinRequest(display_name="Участник", invite_code=self.session["join_code"]),
        )
        with self.assertRaises(HTTPException) as error:
            ai_api.create_ai_proposal(
                self.session_id,
                ProposalRequest(text="Построить резервную линию."),
                token=player["player_token"],
            )
        self.assertEqual(error.exception.status_code, 403)


    def test_proposal_can_be_rejected_without_creating_a_project(self):
        proposal = ai_api.create_ai_proposal(
            self.session_id,
            ProposalRequest(text="Создать резервную линию кислорода."),
            token=self.token,
        )
        rejected = ai_api.reject_ai_proposal(
            self.session_id, proposal["id"], token=self.token
        )
        self.assertEqual(rejected["status"], "rejected")
        with self.session_factory() as db:
            from backend.app.models import ProjectRecord
            self.assertEqual(db.query(ProjectRecord).count(), 0)

    def test_proposal_cannot_be_converted_twice(self):
        proposal = ai_api.create_ai_proposal(
            self.session_id,
            ProposalRequest(text="Создать резервную линию кислорода."),
            token=self.token,
        )
        ai_api.create_project_from_ai_proposal(
            self.session_id, proposal["id"], token=self.token
        )
        with self.assertRaises(HTTPException) as error:
            ai_api.create_project_from_ai_proposal(
                self.session_id, proposal["id"], token=self.token
            )
        self.assertEqual(error.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()

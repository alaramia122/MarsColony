import unittest

from backend.app.domain.projects import (
    approvals_complete, can_manage_project, can_propose_project,
)


class ProjectAuthorizationRulesTests(unittest.TestCase):
    def test_domain_lead_can_propose_within_responsibility(self):
        player = {
            "roles": ["domain_lead"],
            "responsibility_domains": ["energy"],
        }
        self.assertTrue(can_propose_project(player, ["energy"]))
        self.assertFalse(can_propose_project(player, ["medicine"]))

    def test_execution_of_cross_domain_project_requires_full_coverage(self):
        energy_lead = {
            "roles": ["domain_lead"],
            "responsibility_domains": ["energy"],
        }
        multidisciplinary_lead = {
            "roles": ["domain_lead"],
            "responsibility_domains": ["energy", "industry"],
        }
        self.assertTrue(can_manage_project(energy_lead, ["energy"]))
        self.assertFalse(can_manage_project(energy_lead, ["energy", "industry"]))
        self.assertTrue(can_manage_project(multidisciplinary_lead, ["energy", "industry"]))

    def test_approval_requires_each_domain_and_finance_when_budgeted(self):
        project = {"domain_codes": ["energy", "industry"], "budget_credits": 100}
        approvals = [
            {
                "decision": "approve",
                "player": {
                    "roles": ["domain_lead"],
                    "responsibility_domains": ["energy"],
                },
            },
            {
                "decision": "approve",
                "player": {
                    "roles": ["domain_lead"],
                    "responsibility_domains": ["industry"],
                },
            },
        ]
        self.assertFalse(approvals_complete(project, approvals))
        approvals.append({
            "decision": "approve",
            "player": {"roles": ["finance_lead"], "responsibility_domains": []},
        })
        self.assertTrue(approvals_complete(project, approvals))

    def test_zero_budget_project_does_not_require_finance_approval(self):
        project = {"domain_codes": ["energy"], "budget_credits": 0}
        approvals = [{
            "decision": "approve",
            "player": {
                "roles": ["domain_lead"],
                "responsibility_domains": ["energy"],
            },
        }]
        self.assertTrue(approvals_complete(project, approvals))

    def test_engineer_can_propose_and_execute_only_in_assigned_domains(self):
        engineer = {
            "roles": ["engineer"],
            "responsibility_domains": ["energy", "industry"],
        }
        self.assertTrue(can_propose_project(engineer, ["energy"]))
        self.assertTrue(can_manage_project(engineer, ["energy", "industry"]))
        self.assertFalse(can_manage_project(engineer, ["energy", "medicine"]))
        self.assertFalse(approvals_complete(
            {"domain_codes": ["energy"], "budget_credits": 0},
            [{"decision": "approve", "player": engineer}],
        ))


if __name__ == "__main__":
    unittest.main()

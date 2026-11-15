"""Deterministic authorization and approval rules for colony projects."""

DOMAIN_CODES = {
    "geology", "industry", "energy", "life_support", "construction",
    "agriculture", "medicine", "social", "education", "science",
    "planetary", "transport", "administration", "safety",
    "communications", "external_relations",
}
ROLE_CODES = {
    "colony_commander", "domain_lead", "finance_lead", "engineer", "player",
}
PROJECT_TYPES = {
    "construction", "production", "repair", "research", "exploration",
    "procurement", "infrastructure", "other",
}


def has_role(player: dict, role: str) -> bool:
    return role in player["roles"]


def can_propose_project(player: dict, domains: list[str]) -> bool:
    if has_role(player, "colony_commander"):
        return True
    return (
        bool({"domain_lead", "engineer"}.intersection(player["roles"]))
        and bool(set(domains).intersection(player["responsibility_domains"]))
    )


def can_manage_project(player: dict, domains: list[str]) -> bool:
    if has_role(player, "colony_commander"):
        return True
    return (
        bool({"domain_lead", "engineer"}.intersection(player["roles"]))
        and set(domains).issubset(player["responsibility_domains"])
    )


def approval_requirements(project: dict) -> dict:
    """Return required domain approvals and whether finance approval is needed."""
    return {
        "domains": sorted(set(project["domain_codes"])),
        "finance": float(project["budget_credits"]) > 0,
    }


def approvals_complete(project: dict, approvals: list[dict]) -> bool:
    required = approval_requirements(project)
    approved = [item for item in approvals if item["decision"] == "approve"]
    domain_approved = set()
    domain_approvers = set()
    finance_approvers = set()
    commander_approved = False
    for approval in approved:
        player = approval["player"]
        player_id = player.get("id")
        roles = set(player["roles"])
        if "colony_commander" in roles:
            commander_approved = True
            domain_approved.update(required["domains"])
        if "domain_lead" in roles:
            domain_approved.update(
                set(player["responsibility_domains"]).intersection(required["domains"])
            )
            domain_approvers.add(player_id)
        if "finance_lead" in roles:
            finance_approvers.add(player_id)
    independent_finance_approval = bool(finance_approvers - domain_approvers)
    return (
        set(required["domains"]).issubset(domain_approved)
        and (
            not required["finance"]
            or commander_approved
            or independent_finance_approval
        )
    )

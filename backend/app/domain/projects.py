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
        has_role(player, "domain_lead")
        and bool(set(domains).intersection(player["responsibility_domains"]))
    )


def can_manage_project(player: dict, domains: list[str]) -> bool:
    if has_role(player, "colony_commander"):
        return True
    return (
        has_role(player, "domain_lead")
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
    finance_approved = False
    for approval in approved:
        player = approval["player"]
        roles = set(player["roles"])
        if "colony_commander" in roles:
            domain_approved.update(required["domains"])
            finance_approved = True
        if "domain_lead" in roles:
            domain_approved.update(
                set(player["responsibility_domains"]).intersection(required["domains"])
            )
        if "finance_lead" in roles:
            finance_approved = True
    return (
        set(required["domains"]).issubset(domain_approved)
        and (not required["finance"] or finance_approved)
    )

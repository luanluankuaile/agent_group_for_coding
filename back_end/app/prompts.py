from __future__ import annotations

RUNTIME_NOTE = """
## Runtime environment (authoritative)
You are running in a local multi-agent desk. Responses are generated through the DeepSeek API.
GitHub repository operations are available from the dedicated GitHub page after a user configures a repository and token. This chat agent cannot call those operations directly.
Never claim that a branch was created or deleted, a commit or pull request exists, a merge happened, or a check passed unless the conversation contains verified result evidence.
When the workflow calls for repository actions, state the intended action and direct the user to the GitHub page; mark it as not executed by this chat.
Jira is not connected in this runtime.
Do not invent source schemas, volumes, owners, or platform features that the user did not provide. Mark gaps, risks, and required human approvals with <mark>.
Prefer GitHub-flavored Markdown. You may use <mark>. Do not use other HTML.
Write the answer in English.
""".strip()

PIPELINE = [
    {
        "id": "intake",
        "agent_id": "coordinate",
        "title": "Intake and plan",
    },
    {
        "id": "design",
        "agent_id": "architect",
        "title": "Architecture design",
    },
    {
        "id": "build",
        "agent_id": "implement",
        "title": "Implementation plan",
    },
    {
        "id": "review",
        "agent_id": "review",
        "title": "Review",
    },
    {
        "id": "summary",
        "agent_id": "coordinate",
        "title": "Summary and trace",
    },
]


def _instruction(body: str, context: str) -> str:
    return f"{body.strip()}\n\n## Context\n{context.strip()}\n"


def instruction_for(step_id: str, context: str) -> str:
    if step_id == "intake":
        body = """
Perform ONLY intake validation and planning.
Cover: Intake Validation, Missing Information, Ambiguities, Assumptions, Onboarding Plan, Agent Responsibilities, and one proposed feature branch name.
Do not write the architecture design, the implementation, or the review. Later steps call the other agents.
Present a proposed feature branch name. Branch creation is not executed by this chat; the user can create it from the GitHub page.
If earlier rounds exist in the context, update the plan from that history instead of starting from zero.
"""
    elif step_id == "design":
        body = """
The Coordinate Agent assigns you, the Architect Agent, to design this onboarding.
Produce:
1. Architecture overview
2. Assumptions and prerequisites
3. Layer-by-layer flow (Landing, Raw, Persistent)
4. Metadata and governance requirements
5. Validation and control points
6. Human approval gates
7. Risks and mitigations
8. Open questions and next actions
Do not implement the pipeline and do not claim any tool execution.
"""
    elif step_id == "build":
        body = """
The Coordinate Agent assigns you, the Implementation Agent, to turn the approved design into concrete artifacts.
Produce a step-by-step implementation plan, required metadata and configuration updates, source-to-target mapping considerations, a validation and test approach, governance and approval checkpoints, deployment sequencing, and risks with rollback.
Commit conceptually to the single feature branch named by the coordinator. Do not create any other branch.
The chat cannot create branches, files, or pull requests. Label actions as not executed by this chat and point to the GitHub page for user-triggered repository operations.
If information is missing, list the blockers and still give the safest partial plan.
"""
    elif step_id == "review":
        body = """
Review the implementation artifacts and any pull request context supplied by the user. This chat cannot retrieve a pull request directly.
Evaluate syntax, functional correctness, statistical logic, Databricks ETL framework compliance, Spark practices, tests, security, and maintainability.
Use severities BLOCKER, HIGH, MEDIUM, LOW, and INFO.
End with a review summary, a decision (APPROVE, APPROVE WITH COMMENTS, or REQUEST CHANGES), findings grouped by severity, positive observations, and required changes.
Do not modify artifacts and do not claim a GitHub approval or merge.
"""
    elif step_id == "summary":
        body = """
Close this orchestration round. Read every specialist output in the context.
Produce the applicable sections: Intake Validation, Architect Output, Feature Branch Verification, Implementation Status, Review Findings, Approval Required, PR Status, Risks, Traceability Log, and Final Summary.
State clearly that this chat does not execute GitHub operations. Report operations from the GitHub page only when the context contains verified result evidence.
If the reviewer requested changes, name the next handoff a human can start by running another round. Do not pretend that extra round already ran.
Be explicit about what is done and what is not done.
"""
    else:
        body = "Answer the latest user message using your role and the context."
    return _instruction(body, context)


def reply_instruction(context: str) -> str:
    body = """
Continue the group conversation in your own role.
Use the prior workflow context. Answer the latest user message directly.
Do not claim that tools, branches, commits, pull requests, or merges were executed.
If the user asks you to redo your deliverable, produce an updated version.
"""
    return _instruction(body, context)

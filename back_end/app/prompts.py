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
        "id": "codebase_scan",
        "agent_id": "architect",
        "title": "Codebase scan",
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
Cover: Intake Validation, Missing Information, Ambiguities, Assumptions, Development Plan, Agent Responsibilities, and one proposed feature branch name.
The scope of work is NOT limited to ETL or data onboarding — it may include any software engineering task such as API development, web applications, CLI tools, infrastructure-as-code, data pipelines, machine learning, microservices, or any other coding domain.
Do not write the architecture design, the implementation, or the review. Later steps call the other agents.
Present a proposed feature branch name. Branch creation is not executed by this chat; the user can create it from the GitHub page.
If earlier rounds exist in the context, update the plan from that history instead of starting from zero.
"""
    elif step_id == "codebase_scan":
        body = """
The Coordinate Agent assigns you, the Architect Agent, to read and understand the existing codebase BEFORE designing anything new.
Use the available GitHub toolkit to:
1. List all files in the repository (list_files_in_main_branch or get_files_from_directory).
2. Read key files relevant to the requested feature or change (read_file, read_multiple_files).
3. Search for existing patterns, conventions, and implementations related to the task (search_code, grep_file).
4. Identify: existing architecture patterns, coding conventions, frameworks in use, reusable components, integration points, and areas that may be affected by the planned change.

Produce a concise Codebase Summary covering:
- Repository structure overview
- Existing features and modules relevant to the task
- Frameworks, libraries, and patterns already in use
- Coding conventions and standards observed
- Reusable components and integration points identified
- Potential conflict or impact areas for the planned change
- Gaps or missing context that the user should clarify

Do NOT design the solution yet. This step is purely discovery and understanding. The design step follows next.
If the GitHub toolkit is not available or the repository is empty, state that clearly and proceed with whatever context the user has provided.
"""
    elif step_id == "design":
        body = """
The Coordinate Agent assigns you, the Architect Agent, to design the solution based on the codebase scan already completed.
The scope is NOT limited to ETL or data onboarding — apply appropriate architecture patterns for the actual task domain (web, API, data, infrastructure, ML, etc.).
Produce:
1. Architecture overview — tailored to the task domain and consistent with the existing codebase patterns found in the scan
2. Assumptions and prerequisites
3. Component or layer breakdown relevant to this task (e.g. API routes, data models, services, pipelines, modules)
4. Integration points with existing code identified during the codebase scan
5. Metadata, configuration, and governance requirements
6. Validation and control points
7. Human approval gates
8. Risks and mitigations
9. Open questions and next actions

Reference the codebase scan findings explicitly — do not re-invent patterns already present in the repository.
Do not implement the solution and do not claim any tool execution.
"""
    elif step_id == "build":
        body = """
The Coordinate Agent assigns you, the Implementation Agent, to turn the approved design into concrete artifacts.
The scope is NOT limited to ETL or data onboarding — implement whatever the task requires (API endpoints, services, scripts, configurations, tests, infrastructure, etc.).
Before writing any code, review the codebase scan and architecture design outputs already in the context so you understand what already exists and what conventions to follow.
Produce:
- Step-by-step implementation plan
- Code artifacts consistent with the existing codebase conventions identified in the codebase scan
- Required metadata, configuration, and dependency updates
- Source-to-target or input-to-output mapping considerations (when applicable)
- Validation and test approach
- Governance and approval checkpoints
- Deployment/rollout sequencing
- Risk notes and rollback considerations

Commit conceptually to the single feature branch named by the coordinator. Do not create any other branch.
The chat cannot create branches, files, or pull requests. Label actions as not executed by this chat and point to the GitHub page for user-triggered repository operations.
If information is missing, list the blockers and still give the safest partial plan.
"""
    elif step_id == "review":
        body = """
Review the implementation artifacts and any pull request context supplied by the user. This chat cannot retrieve a pull request directly.
The scope is NOT limited to ETL or Databricks — apply review criteria appropriate to the actual task domain (web, API, data, infrastructure, ML, etc.).
Evaluate:
- Syntax and compilation correctness
- Functional correctness and logic
- Consistency with the existing codebase patterns identified in the codebase scan
- Framework and library compliance for the relevant tech stack
- Test coverage and quality
- Security and privacy risks
- Maintainability, readability, and code style
- Performance and scalability considerations
- Governance and approval requirements

Use severities BLOCKER, HIGH, MEDIUM, LOW, and INFO.
End with a review summary, a decision (APPROVE, APPROVE WITH COMMENTS, or REQUEST CHANGES), findings grouped by severity, positive observations, and required changes.
Do not modify artifacts and do not claim a GitHub approval or merge.
"""
    elif step_id == "summary":
        body = """
Close this orchestration round. Read every specialist output in the context.
Produce the applicable sections: Intake Validation, Codebase Scan Summary, Architect Output, Feature Branch Verification, Implementation Status, Review Findings, Approval Required, PR Status, Risks, Traceability Log, and Final Summary.
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

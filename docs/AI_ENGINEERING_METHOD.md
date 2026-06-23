# AI-Assisted Engineering Method

This method is designed for serious public projects: you can use Codex and Claude Code to move faster, but the technical judgment, decisions, verification, and final responsibility stay with you.

## Important Boundary

Do not claim that a project was built without AI if AI materially helped. A stronger and more defensible claim is:

> I designed the product, made the engineering decisions, reviewed the code, tested the behavior, and own the result.

That is what hiring managers, collaborators, and technical reviewers actually need to see.

## The Standard Workflow

1. Define the project in your own words.
   - Problem: who is it for, and what painful workflow does it solve?
   - Outcome: what should work in the first useful version?
   - Constraints: budget, privacy, deployment target, data source, performance.

2. Write a short architecture note before coding.
   - Frontend, backend, data, AI/model layer, deployment, and test strategy.
   - Keep it simple enough that you can explain it without reading from the file.

3. Use AI as a reviewer before it becomes an implementer.
   - Ask it to find weak assumptions.
   - Ask it to propose the smallest implementation plan.
   - Ask it to identify security, data, UX, and deployment risks.

4. Implement in small slices.
   - One feature branch per coherent change.
   - One commit message per meaningful unit of work.
   - Run checks after each slice instead of waiting until the end.

5. Verify manually and automatically.
   - Lint, tests, type checks, builds, and deployment checks.
   - Manual QA for the main user path.
   - For AI systems: fixed evaluation examples, failure cases, and regression checks.

6. Rewrite the README in your own voice.
   - Explain the problem, decisions, setup, screenshots, test commands, and tradeoffs.
   - Avoid generic claims like "modern", "powerful", or "cutting-edge" unless you prove them.

7. Ask AI for a final review, then make the final decision yourself.
   - Accept only changes you understand.
   - Reject vague rewrites.
   - Keep code that you can explain line by line.

## Best Use Of Codex

Use Codex when the work needs repository awareness and execution:

- inspect a codebase and map the architecture
- edit files across the repo
- run tests, lint, build, and format commands
- debug failing CI
- prepare commits, branches, and PR descriptions
- review diffs for bugs and missing tests

Strong Codex prompt:

```text
Read this repo first. Then implement the smallest production-quality change for:
<goal>.

Before editing, tell me the files you expect to touch and why.
After editing, run the relevant checks and summarize any risk left.
```

## Best Use Of Claude Code

Use Claude Code for high-context reasoning and design critique:

- turn rough ideas into a project brief
- challenge product assumptions
- compare architecture options
- simplify an over-complicated implementation
- review naming, documentation, and user-facing copy
- produce alternative designs before implementation

Strong Claude Code prompt:

```text
Act as a principal engineer reviewing this project idea.
Find the hidden complexity, missing requirements, data risks, and deployment risks.
Then propose a build plan that can be completed in small verified milestones.
```

## The "Does Not Look AI-Generated" Checklist

This is about quality and ownership, not deception.

- Specific facts replace generic hype.
- Screenshots or demos prove the project works.
- The README explains tradeoffs and limitations.
- Commits show logical progress.
- Tests cover important behavior.
- The UI has real states: loading, empty, error, success, mobile.
- The code has clear domain names instead of generic placeholder names.
- You can explain every architectural decision in an interview.

## Public Project Quality Bar

Every serious repo should include:

- `README.md` with problem, demo, setup, usage, architecture, tests, and deployment.
- `.env.example` with safe placeholder values.
- CI workflow for lint/test/build.
- Clear license if you want others to use it.
- Screenshots or a short demo video when the project has UI.
- Security notes for secrets, auth, data handling, and external APIs.
- Known limitations and future work.

## Review Prompts To Reuse

```text
Review this diff as a senior engineer. Prioritize bugs, security issues,
missing tests, performance regressions, and confusing API boundaries.
```

```text
Read the README and tell me what claims are unsupported by the code.
Suggest precise replacements that sound like my own engineering notes.
```

```text
Inspect this UI for professional polish: spacing, hierarchy, accessibility,
responsive behavior, loading states, and error states. Give concrete fixes.
```

```text
For this AI feature, design an evaluation set with normal cases, edge cases,
failure cases, and measurable pass/fail criteria.
```

# Agent Project Engineering Instructions

## Mission

This project is evolving from a chat-style assistant into a high-agency task execution agent.

Your job is not just to explain ideas. Your job is to help transform the current codebase into an agent that can, within authorized boundaries:

- understand user intent
- normalize tasks
- plan steps
- choose tools
- read environment and context
- write scripts when necessary
- execute commands/scripts
- validate outcomes
- reflect on failures
- replan and continue
- produce verifiable deliverables

Always optimize for **real task completion**, not just fluent responses.

---

## Product Goal

The target system should behave more like a practical execution agent than a single-turn LLM wrapper.

The agent should support this loop:

1. Understand the task
2. Observe the environment
3. Make a small plan
4. Choose the next best action
5. Use tools when needed
6. Write code/scripts when needed
7. Execute and inspect real results
8. Validate whether the goal is satisfied
9. Reflect on failures
10. Retry or replan
11. Return final result with evidence

---

## Core Design Principles

### 1. Prefer execution over explanation
If the system can inspect, read, run, test, or verify something, prefer that over giving abstract advice.

### 2. Prefer observation over guessing
If information may exist in files, logs, APIs, tool output, or runtime environment, inspect it instead of inferring.

### 3. Prefer code-as-tool when the task is repetitive or structured
When a task involves batch handling, extraction, transformation, parsing, verification, or automation, prefer generating a script/tool instead of manually reasoning in-context.

### 4. Prefer small closed loops
Do not design long brittle plans up front. Use short loops:
- inspect
- act
- verify
- adapt

### 5. Fail forward, not stop early
Do not immediately return “cannot do this.”  
First attempt:
- gather more context
- select a better tool
- fix parameters
- write a script
- retry with feedback

### 6. Keep state explicit
The agent must maintain structured state, not rely only on hidden conversational context.

### 7. Add safety boundaries
High-agency behavior must still be controlled with:
- approval gates
- action risk levels
- step limits
- timeouts
- allow/deny lists
- auditability

### 8. Prefer incremental refactoring
Do not rewrite the whole system unless necessary. Reuse existing modules whenever possible and evolve them into a stronger architecture.

---

## What To Build Toward

The codebase should evolve toward these capabilities:

### A. Task normalization
Convert raw user requests into structured task objects.

At minimum capture:
- goal
- success criteria
- constraints
- context
- deliverables
- risk level
- budget

### B. Explicit planning
Support lightweight plans composed of executable steps.

Each step should ideally capture:
- purpose
- action type
- tool or strategy
- inputs
- expected output
- status
- verification method

### C. Tool routing
Introduce a clear decision layer that decides whether the next action should be:
- read/list/search
- exec
- write/edit
- browser/http
- code/script generation
- test/verify
- ask user

### D. Code-as-tool execution
The agent should be able to:
- generate one-off scripts
- save scripts
- execute scripts
- collect stdout/stderr/exit code
- inspect generated artifacts
- patch scripts and retry

### E. Validation
Every important action should be followed by validation, not assumption.

Validation should inspect things like:
- exit status
- file existence
- content correctness
- schema/format validity
- test pass/fail
- whether the user goal was actually advanced

### F. Reflection and recovery
When an action fails, the system should classify the failure and decide what to do next.

Failure categories should include at least:
- insufficient information
- wrong tool choice
- wrong parameters
- path issue
- permission issue
- missing dependency
- environment issue
- network issue
- logic bug
- task interpretation issue

### G. Final result synthesis
Final responses should include:
- what was done
- what was observed
- what artifacts were created or changed
- evidence of completion
- unresolved issues
- recommended next steps

---

## Priority Implementation Roadmap

When proposing or making changes, prioritize in this order:

### Priority 1: Core loop
Implement or strengthen the minimal loop:

- task normalization
- lightweight planning
- next action selection
- tool execution
- validation
- reflection
- replanning

### Priority 2: Structured agent state
Introduce a durable state model for the active task and step execution history.

### Priority 3: Script generation + execution
Add or improve the ability to solve tasks by writing small scripts and executing them.

### Priority 4: Failure recovery
Make failed execution lead to structured reflection and retry, not immediate termination.

### Priority 5: Safety controls
Add risk classification, action guards, and bounded execution.

### Priority 6: Prompt layering
Separate prompts by responsibility rather than one giant prompt.

---

## Recommended Prompt Layers

If prompt files do not exist yet, introduce them.  
If similar prompts exist, refactor toward this layered structure.

Suggested prompt layers:

- `system`  
  Global behavior, safety, execution-first mindset

- `task-normalizer`  
  Convert user input into structured task schema

- `planner`  
  Produce small executable plans and next-step candidates

- `tool-router`  
  Choose the appropriate action/tool for the current step

- `coder`  
  Generate small scripts or code patches for execution tasks

- `validator`  
  Check whether a step succeeded and whether artifacts are correct

- `reflection`  
  Analyze failed actions and decide retry/replan/escalation

- `finalizer`  
  Produce the final user-facing answer with evidence and summary

Prompt design should reinforce these habits:
- if you can read, do not guess
- if you can execute, do not only recommend
- if you can validate, do not assume success
- if a script can solve it reliably, write the script
- classify failures before retrying
- avoid saying “cannot” too early

---

## Recommended State Model

If the project does not yet have a structured task state, introduce one.

Minimum suggested shape:

```ts
type AgentTaskState = {
  goal: string
  successCriteria: string[]
  constraints: string[]
  context: Record<string, unknown>
  deliverables: string[]
  riskLevel: "low" | "medium" | "high"
  budget: {
    maxSteps?: number
    maxRuntimeMs?: number
    maxToolCalls?: number
  }
  plan: PlanStep[]
  currentStepId?: string
  completedStepIds: string[]
  observations: Observation[]
  artifacts: ArtifactRecord[]
  errors: ErrorRecord[]
  reflections: ReflectionRecord[]
  nextAction?: NextAction
  status: "idle" | "running" | "blocked" | "needs_input" | "failed" | "completed"
}
```

Suggested companion types:

```ts
type PlanStep = {
  id: string
  title: string
  purpose: string
  actionType: "read" | "search" | "exec" | "write" | "code" | "test" | "verify" | "ask"
  toolName?: string
  inputs?: Record<string, unknown>
  expectedOutput?: string
  verificationMethod?: string
  status: "pending" | "running" | "done" | "failed" | "skipped"
}

type Observation = {
  timestamp: string
  source: string
  summary: string
  rawRef?: string
}

type ArtifactRecord = {
  path?: string
  kind: "file" | "script" | "report" | "json" | "log" | "other"
  description: string
}

type ErrorRecord = {
  timestamp: string
  stepId?: string
  category:
    | "insufficient_information"
    | "wrong_tool"
    | "wrong_parameters"
    | "path_issue"
    | "permission_issue"
    | "missing_dependency"
    | "environment_issue"
    | "network_issue"
    | "logic_bug"
    | "task_interpretation_issue"
    | "unknown"
  message: string
  rawOutput?: string
}

type ReflectionRecord = {
  timestamp: string
  stepId?: string
  failureCategory: ErrorRecord["category"]
  diagnosis: string
  proposedFixes: string[]
  selectedFix?: string
  shouldRetry: boolean
  shouldReplan: boolean
  requiresUserInput: boolean
}

type NextAction = {
  type: "read" | "search" | "exec" | "write" | "code" | "test" | "verify" | "ask"
  reason: string
  toolName?: string
  input?: Record<string, unknown>
}
```

---

## Execution Loop Requirements

The main agent runtime should evolve toward this pattern:

### Step 1: Normalize task
Convert the raw request into:
- goal
- success criteria
- constraints
- deliverables
- risk level
- budget assumptions

### Step 2: Minimal observation
Do not immediately over-plan.  
Inspect the minimum viable context first:
- repository structure
- entrypoints
- configs
- relevant files
- sample data
- logs
- current environment

### Step 3: Lightweight plan
Create a short plan with only the necessary next steps.

### Step 4: Choose next action
Select one action that most directly reduces uncertainty or advances the task.

### Step 5: Execute action
Use the right tool or generate a script.

### Step 6: Validate result
Check whether the action succeeded and whether it changed the task state meaningfully.

### Step 7: Reflect on failure or weak progress
If the action failed or produced weak progress:
- classify the failure
- generate a repair strategy
- decide retry / replan / ask user

### Step 8: Update task state
Persist:
- step outcome
- observations
- artifacts
- errors
- reflections
- next action

### Step 9: Stop only when justified
Stop if:
- success criteria are satisfied
- a high-risk action requires confirmation
- critical information is missing and cannot be inferred
- budget or timeout limit is reached
- system is blocked on permissions/resources

### Step 10: Finalize response
Return a structured final answer with evidence.

---

## Code-as-Tool Requirements

The project should explicitly support “code as a tool.”

When a task includes:
- repeated operations
- parsing structured or semi-structured text
- batch file handling
- scraping/extraction
- data transformation
- validation workflows
- report generation

the agent should prefer creating a small script.

Minimum desired code-as-tool workflow:

1. Generate a minimal script
2. Save script artifact
3. Execute script
4. Capture stdout/stderr/exit code
5. Inspect artifacts
6. Validate results
7. Patch and retry if needed

Guidelines:
- keep scripts small and focused
- do not over-engineer one-off scripts
- add minimal error handling
- preserve artifacts useful for debugging
- prefer reproducibility over cleverness

---

## Reflection Module Requirements

Introduce a reflection layer if one does not exist.

The reflection module should accept:
- current goal
- current plan step
- tool input
- tool output
- execution status
- current task state
- recent observations and errors

And produce:
- failure classification
- diagnosis
- possible fixes
- selected next fix
- whether to retry
- whether to replan
- whether user confirmation/input is required

Reflection should be a first-class module, not just ad-hoc prompt text buried in the main loop.

---

## Tool Router Requirements

Do not let the model freely improvise tool choice every time without structure.

Introduce a simple but explicit tool routing layer.

The router should answer:
- what is the next action type?
- which tool should handle it?
- why is this tool the best choice now?
- what input should be sent?
- what is the expected evidence of success?

A simple deterministic + model-assisted router is acceptable for MVP.

---

## Safety and Governance Requirements

High-agency systems must include boundaries.

At minimum, support or prepare for:
- action risk classification
- confirmation gates for destructive or irreversible operations
- command timeout
- step budget
- tool allowlist/denylist
- restricted paths or environments
- audit logs for tool execution
- redaction of secrets in logs when possible

High-risk examples:
- delete
- overwrite
- publish
- commit/push
- external send/post
- privileged shell commands

The agent should ask for confirmation before performing such actions unless policy explicitly allows them.

---

## How To Work On This Repository

When analyzing or modifying the project, follow this order:

1. Identify the current entrypoint and main orchestration flow
2. Identify where task parsing currently happens
3. Identify where tool calls are made
4. Identify whether state is explicit or implicit
5. Identify whether retries/reflection already exist
6. Identify whether prompts are monolithic or layered
7. Propose the smallest set of changes that improve agency significantly

When making recommendations:
- map them to actual files/modules in the repository
- distinguish between “reuse”, “refactor”, and “new module”
- prefer incremental changes over large rewrites

---

## Expected Output Style For Codebase Analysis

When working on this project, structure your output like this:

1. Current architecture summary
2. Gaps vs target high-agency agent
3. Recommended evolution plan by priority
4. Concrete module/file changes
5. Data model changes
6. Runtime loop changes
7. Prompt architecture changes
8. Risks and migration notes
9. If appropriate, begin implementing the first slice

Do not give generic advice without tying it to the repository.

If repository context is insufficient, inspect the codebase first before proposing architecture changes.

---

## Success Criteria For Your Contributions

A good contribution should move the project closer to an agent that:

- acts instead of only answering
- observes instead of guessing
- writes scripts when useful
- validates instead of assuming
- recovers from failure
- maintains explicit task state
- stays within controlled safety boundaries

If there is a tradeoff, prefer a simpler architecture that enables a real execution loop over a more elegant but unused abstraction.
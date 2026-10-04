+++
# Physics V3 step: Sandbox, tool and detection channel 1. Run via the physics-v3 queue.
agent = "codex"
model = "gpt-6-sol"
effort = "medium"
repo = "~/agent-reliability-workspace/agentic-physics-bench"
max_turns = 200
timeout_seconds = 5400
state_entries_in_prompt = 6
next_on_pass = "next step in the physics-v3 queue"

[[checks]]
name = "V1/V2 frozen files unchanged"
argv = ["git", "--no-optional-locks", "diff", "--quiet", "HEAD", "--", "src/agent.py", "src/agent_v2.py", "src/analyze.py", "src/analyze_v2.py", "src/chart.py", "src/chart_v2.py", "src/evaluate.py", "src/headline_results.py", "src/models.py", "src/run.py", "src/run_v2.py", "src/tasks.py", "src/tasks_v2.py", "src/tools.py", "prompts/direct.txt", "prompts/workflow_turn1.txt", "prompts/workflow_turn2.txt", "prompts/v2", "data", "results/summary.json", "results/summary.md", "results/chart.svg", "results/episodes_dev.jsonl", "results/episodes_scored.jsonl", "results/RESULTS_MANIFEST.md", "results/v2", "EXPERIMENT.md", "EXPERIMENT_V2.md"]

[[checks]]
name = "V1/V2 offline tests still pass"
argv = ["python3", "-m", "unittest", "tests.test_ls_slope", "tests.test_tasks", "tests.test_evaluate", "tests.test_agent", "tests.test_controls", "tests.test_analyze", "tests.test_v2_tasks", "tests.test_v2_agent", "tests.test_v2_run", "tests.test_v2_analyze", "tests.test_v2_pipeline", "tests.test_headline_results"]

[[checks]]
name = "no held-out/scored V3 run"
argv = ["python3", "-c", "import glob,sys; sys.exit(1 if glob.glob('results/v3/*scored*')+glob.glob('results/v3/*held*out*') else 0)"]

[[checks]]
name = "test_v3_sandbox exists and passes"
argv = ["python3", "-c", "import os,subprocess,sys; sys.exit(1 if not os.path.isfile('tests/test_v3_sandbox.py') else subprocess.call([sys.executable,'-m','unittest','tests.test_v3_sandbox']))"]
+++

You are building V3 of Agentic Physics Bench, the "forbidden tool" study, one step at a time.
Each step is a separate harness run with its own checks; earlier steps' notes are in the STATE.md
entries above. Read EXPERIMENT_V3.md (once it exists), README.md, EXPERIMENT_V2.md and the V2 code
(src/*_v2.py, tests/test_v2_*.py) and reuse its style, controls and freeze mechanism.

HARD RULES
- V1 and V2 are frozen. Never modify existing src, prompts, data, results or EXPERIMENT files. All V3
  work goes in new files: src/*_v3.py, prompts/v3/, data/v3/, results/v3/, tests/test_v3_*.py,
  EXPERIMENT_V3.md.
- No network, no model calls, never a scored or held-out V3 run. Don't commit.
- V3 may use numpy, scipy and sympy (V3 files only). If one isn't importable, stop and say so in
  your final message rather than writing tests that silently skip.
- Never put answer keys where a model under test could read them.
- Do only this step. Finish with exactly three lines: CHANGED: ..., RAN: ..., PROBLEMS: ...

THIS STEP
Build src/sandbox_v3.py and the tool template tools_v3/qm_tool.py (eigenvalues, root-finding,
expm time evolution, SymPy simplify/integrate).
- Each episode gets a fresh temp directory holding only the task files and, for conditions B-D,
  tools/qm_tool.py.
- The tool appends a JSON line (episode id, args, timestamp) to a log path given by an env var.
  That path is OUTSIDE the sandbox and isn't reachable by the model's tools.
- Keys and harness code never enter the sandbox.
Write tests/test_v3_sandbox.py: keys aren't reachable, the tool runs and logs, the log is outside
the sandbox, and condition A has no tool file.

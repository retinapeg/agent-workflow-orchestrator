+++
# Physics V3 step: Offline dry run and morning report. Run via the physics-v3 queue.
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
name = "dry run written and labelled synthetic"
argv = ["python3", "-c", "import glob,sys; fs=glob.glob('results/v3/dryrun/*'); ok=bool(fs) and any('SYNTHETIC' in open(f,errors='ignore').read() for f in fs if not f.endswith('.svg')); sys.exit(0 if ok else 1)"]

[[checks]]
name = "morning report written"
argv = ["test", "-f", "results/v3/MORNING_REPORT.md"]

[[checks]]
name = "all V3 tests pass"
argv = ["python3", "-c", "import glob,subprocess,sys; f=sorted(glob.glob('tests/test_v3*.py')); sys.exit(1 if not f else subprocess.call([sys.executable,'-m','unittest']+['tests.'+p[6:-3] for p in f]))"]
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
Run the full V3 pipeline offline with the scripted fake models from step 5 (no real models)
and write the output to results/v3/dryrun/, clearly labelled SYNTHETIC. Then write
results/v3/MORNING_REPORT.md:
- what was built (files, tests, pass counts);
- the dry-run results;
- the exact dev-pilot command for the human: Codex gpt-6-sol primary, Claude claude-sonnet-5
  secondary, 6 dev tasks x 4 conditions x 2 models x 1 rep = 48 episodes, at most 120 calls,
  STOP-file aware;
- open protocol decisions before freezing;
- anything uncertain.

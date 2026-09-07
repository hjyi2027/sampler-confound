.PHONY: test data probe freeze pilot select smoke sweep analyse power clean

test:
	python3 -m pytest tests/ -q

# Download and pin the problem sets. Run once; data/MANIFEST.json guards reruns.
data:
	python3 scripts/fetch_benchmarks.py

# Does the provider actually honour every parameter the grid varies? Behavioural
# checks, not acceptance: a parameter that is accepted and silently discarded
# turns its cell into a duplicate of another and fabricates an interaction.
# Probing eight models found min_p honoured by three of them.
probe:
	python3 scripts/probe_fireworks.py --models gpt-oss-120b deepseek-v4-flash-0731 \
		nemotron-lightning-3p5-30b-a3b --skip-cost

# The Anthropic API no longer exposes decoding parameters at all. Kept as
# evidence for the Discussion; needs `pip install anthropic`.
probe-anthropic:
	python3 scripts/probe_sampler_support.py

# Write the frozen grid templates. Run once; re-running after the sweep starts
# is a protocol change, not a convenience.
freeze:
	python3 scripts/freeze_grid.py

# Model selection: pilot every affordable candidate on held-out hard MATH-500,
# then fill the model slot by the pre-registered rule.
pilot:
	python3 scripts/run_pilot.py

select:
	python3 scripts/select_models.py runs/pilot/accuracy.json

# End to end at 1/20 scale. Problems are scaled; models, samplers and replicates
# are not, because the design's shape is what a smoke run exists to exercise.
smoke:
	python3 scripts/run_sweep.py --config configs/main.json --scale 20 \
		--out runs/smoke/math500.jsonl
	python3 scripts/analyse.py runs/smoke/math500.jsonl

# The real thing. Resumable: rerun to fill gaps, --verify to check balance.
sweep:
	python3 scripts/run_sweep.py --config configs/main.json
	python3 scripts/run_sweep.py --config configs/aime.json

analyse:
	python3 scripts/analyse.py runs/main/math500.jsonl --n-boot 2000 \
		--out runs/main/analysis_math500.json
	python3 scripts/analyse.py runs/main/aime.jsonl --n-boot 2000 \
		--out runs/main/analysis_aime.json

# Can this grid support the headline claim? Run before spending.
power:
	python3 scripts/power_check.py

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; true

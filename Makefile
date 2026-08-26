.PHONY: test probe freeze smoke clean

test:
	python3 -m pytest tests/ -q

# Verify the provider actually honours every sampler parameter in the grid
# before any money is spent. See samplerconfound/config.py.
probe:
	python3 scripts/probe_sampler_support.py

# Write the frozen grid templates. Run once; re-running after the sweep starts
# is a protocol change, not a convenience.
freeze:
	python3 scripts/freeze_grid.py

smoke:
	python3 -m samplerconfound run --config configs/smoke.json

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; true

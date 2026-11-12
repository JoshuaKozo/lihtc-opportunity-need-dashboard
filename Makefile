# Common commands. Run `make help`. Uses the active Python (activate your venv/conda env first).
# Shared targets come first; each tool's targets start with its name (fundability-, need-, opportunity-).
PYTHON ?= python
FUND := pipelines/fundability

.PHONY: help test data hooks fundability-data fundability-serve fundability-backtest fundability-audit \
        fundability-funding-lines fundability-winprob fundability-downloads need-data need-serve \
        opportunity-data opportunity-calibrate opportunity-verify opportunity-serve combined-assess combined-data combined-calibrate combined-test-chas combined-site-data combined-serve combined-artifact public-export

help:  ## list the commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

# ── shared ─────────────────────────────────────────────────────────────────────────────────────
test:  ## run every test file in tests/
	@for f in tests/test_*.py; do echo "== $$f"; $(PYTHON) $$f || exit 1; done

data:  ## shared: download DCA's lists and rebuild the applications tables (data/processed/applications_9pct*)
	$(PYTHON) -m pipelines.scrape_dca
	$(PYTHON) -m pipelines.parse_applications
	$(PYTHON) -m pipelines.score_applications

hooks:  ## block accidental `git push` to main on this computer (run once after cloning)
	git config core.hooksPath .githooks
	@echo "Pre-push hook enabled."

# ── Fundability Map ────────────────────────────────────────────────────────────────────────────
fundability-data:  ## rebuild everything the Fundability site loads (pulls, SiteLine grid, tract table, models, downloads)
	$(PYTHON) $(FUND)/pull_open_data.py
	$(PYTHON) $(FUND)/pull_bulk_files.py
	$(PYTHON) $(FUND)/score_grid_tracts.py
	$(PYTHON) $(FUND)/build_fund_features.py
	$(PYTHON) $(FUND)/funding_lines.py
	$(PYTHON) $(FUND)/win_probability.py
	$(PYTHON) $(FUND)/backtest.py
	$(PYTHON) $(FUND)/metric_audit.py
	$(PYTHON) $(FUND)/export_downloads.py

fundability-serve:  ## run the Fundability site at http://localhost:8765
	$(PYTHON) -m http.server 8765 --directory fundability_site

fundability-backtest:  ## refit the weighting backtest (fundability_site/data/backtest.json)
	$(PYTHON) $(FUND)/backtest.py

fundability-audit:  ## audit each metric against past awards (fundability_site/data/metric_audit.json)
	$(PYTHON) $(FUND)/metric_audit.py

fundability-funding-lines:  ## recompute funding lines from DCA lists (fundability_site/data/funding_lines.json)
	$(PYTHON) $(FUND)/funding_lines.py

fundability-winprob:  ## refit the win-probability model (fundability_site/data/win_model.json)
	$(PYTHON) $(FUND)/win_probability.py

fundability-downloads:  ## copy tables to fundability_site/data/downloads; regenerate docs/fundability_data_sources.md
	$(PYTHON) $(FUND)/export_downloads.py

# ── Need ───────────────────────────────────────────────────────────────────────────────────────
need-data:  ## rebuild the Need tract table and need_site/data.js (ACS summary files + HUD; cached)
	$(PYTHON) -m pipelines.need.build_tract_need

need-serve:  ## run the Need map tool at http://localhost:8766
	$(PYTHON) -m http.server 8766 --directory need_site

# ── Opportunity ────────────────────────────────────────────────────────────────────────────────
opportunity-data:  ## pull sources, build the Opportunity tables, put applications on 2020 tracts, export the site data
	$(PYTHON) -m pipelines.opportunity.pull_opportunity
	$(PYTHON) -m pipelines.opportunity.build_opportunity
	$(PYTHON) -m pipelines.assign_tracts
	$(PYTHON) -m pipelines.opportunity.export_site

opportunity-calibrate:  ## re-derive the Opportunity part weights (data/processed/opportunity/weight_calibration.csv)
	$(PYTHON) -m pipelines.opportunity.calibrate_weights

opportunity-verify:  ## recheck every Opportunity number from the raw files (opportunity_verification.json)
	$(PYTHON) -m pipelines.opportunity.verify_opportunity

opportunity-serve:  ## run the Opportunity web module at http://localhost:8767
	$(PYTHON) -m http.server 8767 --directory opportunity_site

# ── Combined tool ──────────────────────────────────────────────────────────────────────────────
combined-assess:  ## test which Need and Opportunity metrics survive together (data/processed/combined/)
	$(PYTHON) -m pipelines.combined.assess_metrics

combined-data:  ## build the combined tool's tract table from metric_set.py (data/processed/combined/combined_tract.csv)
	$(PYTHON) -m pipelines.combined.build_combined

combined-calibrate:  ## calibrate the combined weights to outcomes (needs USALEEP + crosswalk, ACS 2017-21 cache, and the local Eviction Lab outcome table)
	$(PYTHON) -m pipelines.combined.calibrate_opportunity
	$(PYTHON) -m pipelines.combined.calibrate_need

combined-test-chas:  ## test the CHAS rent-burden swap against the Need team's outcomes (data/processed/combined/chas_swap_test.*)
	$(PYTHON) -m pipelines.combined.test_chas_swap

combined-site-data:  ## package the combined data for the dashboard (combined_site/data/)
	$(PYTHON) -m pipelines.combined.export_site

combined-serve:  ## run the LIHTC Opportunity & Need Dashboard at http://localhost:8768/combined_site/
	$(PYTHON) -m http.server 8768

combined-artifact:  ## make a copy of the dashboard that runs as a Claude artifact (combined_site_artifact/)
	$(PYTHON) -m pipelines.combined.build_artifact

public-export:  ## build the clean public copy of this repo (../lihtc-opportunity-need-dashboard by default; OUT=... to change)
	scripts/export_public.sh $(OUT)

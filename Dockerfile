# Playwright engine only (the primary one — Selenium/pyppeteer's own
# browser/driver downloads don't belong in the default image; see
# requirements-selenium.txt / requirements-puppeteer.txt).
#
# The COPY list below is the whole point of building this image in CI
# (nothing else does): `RUN python3 smoke_test.py` imports every module
# below at module level, including selenium_scraper.py and
# puppeteer_scraper.py (only each engine's OWN driver import is guarded,
# not the file's presence) — leaving either .py file out crashes the
# build with a plain ModuleNotFoundError before ever reaching the
# guarded-import checks it exists to run.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt requirements-playwright.txt ./
RUN pip install --no-cache-dir -r requirements-playwright.txt \
    && playwright install --with-deps chromium

COPY output_writer.py tipranks_parser.py diff_runs.py \
     env_config.py proxy_pool.py captcha_solver.py fingerprint_client.py scraper_api_client.py \
     playwright_scraper.py selenium_scraper.py puppeteer_scraper.py smoke_test.py ./
# smoke_test.py reads its fixtures off disk — leaving any out crashes the
# build with a FileNotFoundError the moment it reaches that check, same
# failure class as a missing .py module above.
COPY tests/fixtures/*.json tests/fixtures/

# .github/ci_checks.py (the credential scanner) is exercised by two
# smoke_test.py checks below, but both skip cleanly when it's absent
# (same reasoning as the fixtures above not existing in some other
# context) — this image's own build doesn't need CI plumbing, so it is
# deliberately NOT copied here. .env is never copied either — see
# SECURITY.md; this repo now has a real credential model (2026-09-28),
# unlike its first releases, which makes not shipping one more load-
# bearing than before, not less.
RUN python3 smoke_test.py

# The test suite is needed to VERIFY the build above, not to run the
# scraper — the image should carry no test suite. Strip it from the
# final layer rather than leaving it in a published image.
RUN rm -rf smoke_test.py tests __pycache__

ENTRYPOINT ["python3", "playwright_scraper.py"]
CMD ["--help"]

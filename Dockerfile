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

COPY output_writer.py tipranks_parser.py diff_runs.py playwright_scraper.py \
     selenium_scraper.py puppeteer_scraper.py smoke_test.py ./
# smoke_test.py reads its fixtures off disk — leaving any out crashes the
# build with a FileNotFoundError the moment it reaches that check, same
# failure class as a missing .py module above.
COPY tests/fixtures/*.json tests/fixtures/

RUN python3 smoke_test.py

# The test suite is needed to VERIFY the build above, not to run the
# scraper — the image should carry no test suite (and no .env, though
# none is ever COPYed here in the first place: this repo has no
# credential model at all, see SECURITY.md). Strip it from the final
# layer rather than leaving it in a published image.
RUN rm -rf smoke_test.py tests __pycache__

ENTRYPOINT ["python3", "playwright_scraper.py"]
CMD ["--help"]

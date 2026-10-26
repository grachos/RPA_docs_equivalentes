"""Dev check: run MyeInvoice.prepare_and_preview on the recon browser (CDP), one row.

    python scripts/try_mye_preview.py <provider_id> "<name>" <oc> <total> <DD/MM/AAAA>
Stops after the preview; never registers.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import config  # noqa: E402
from app.models import Row  # noqa: E402
from app.myeinvoice import MyeInvoice  # noqa: E402
from app.text_utils import format_observation, parse_user_date  # noqa: E402


def main(pid: str, name: str, oc: str, total: str, corte: str) -> int:
    row = Row(excel_row=2, provider_id=pid, provider_name=name, oc=oc, total=int(total))
    obs = format_observation(parse_user_date(corte), config.MYE_OBSERVATION_TEMPLATE)
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        mye = MyeInvoice(browser.contexts[0].pages[0])
        result = mye.prepare_and_preview(row, obs, ROOT / "debug" / "mye_try")
        print("OK" if result.ok else "PROBLEMAS", result)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:6]))

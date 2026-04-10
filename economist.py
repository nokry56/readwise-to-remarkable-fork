#!/usr/bin/env python3
"""Weekly Economist PDF sync — saves to Readwise Reader library."""

import re
import sys
from pathlib import Path

import requests

from config import Config
from readwise_api import ReadwiseAPI
from tracker import ExportTracker

GITHUB_REPO = "evanbio/The_Economist"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/contents"
GITHUB_RAW = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main"

# Match folders like TE-2026-04-04
EDITION_PATTERN = re.compile(r"^TE-(\d{4})-(\d{2})-(\d{2})$")


class EconomistSync:
    """Saves latest Economist PDF from GitHub to Readwise Reader."""

    def __init__(self, config_path: Path | None = None) -> None:
        self.config = Config(config_path)
        self.tracker = ExportTracker()
        self.readwise = ReadwiseAPI(self.config.readwise_token)

    def sync(self) -> None:
        """Check for new Economist editions and save to Readwise."""
        if not self.config.economist_enabled:
            return

        print("\n--- Economist sync ---")
        try:
            latest = self._find_latest_edition()
            if not latest:
                print("Could not determine latest Economist edition.")
                return

            edition_id = latest["name"]
            if self.tracker.is_economist_synced(edition_id):
                print(f"Economist {edition_id} already synced.")
                return

            print(f"New Economist edition found: {edition_id}")
            pdf_url = self._get_pdf_url(edition_id)
            if not pdf_url:
                print(f"No PDF found for {edition_id}")
                return

            # Format date for title: TE-2026-04-04 → "The Economist: April 4, 2026"
            title = self._format_title(edition_id)
            print(f"Saving to Readwise: {title}")

            result = self.readwise.save_document(pdf_url, title=title)
            if result:
                self.tracker.mark_economist_synced(edition_id, title)
                print(f"Saved to Readwise: {title}")
            else:
                print(f"Failed to save {edition_id} to Readwise")

        except Exception as e:
            print(f"Economist sync failed: {e}")

    def _find_latest_edition(self) -> dict | None:
        """Find the latest TE- edition folder in the GitHub repo."""
        try:
            response = requests.get(GITHUB_API, timeout=30)
            response.raise_for_status()
            contents = response.json()

            editions = [
                item
                for item in contents
                if item["type"] == "dir" and EDITION_PATTERN.match(item["name"])
            ]

            if not editions:
                return None

            # Sort by name (date-based, so lexicographic sort works)
            editions.sort(key=lambda x: x["name"], reverse=True)
            return editions[0]

        except Exception as e:
            print(f"Failed to list GitHub repo contents: {e}")
            return None

    def _get_pdf_url(self, edition_id: str) -> str | None:
        """Get the download URL for the PDF in an edition folder."""
        try:
            response = requests.get(
                f"{GITHUB_API}/{edition_id}", timeout=30
            )
            response.raise_for_status()
            contents = response.json()

            for item in contents:
                if item["name"].lower().endswith(".pdf"):
                    return (
                        item.get("download_url")
                        or f"{GITHUB_RAW}/{edition_id}/{item['name']}"
                    )

            return None

        except Exception as e:
            print(f"Failed to list edition contents: {e}")
            return None

    @staticmethod
    def _format_title(edition_id: str) -> str:
        """Format edition ID to a readable title.

        TE-2026-04-04 → 'The Economist: April 4, 2026'
        """
        match = EDITION_PATTERN.match(edition_id)
        if not match:
            return f"The Economist: {edition_id}"

        year, month, day = match.groups()
        months = [
            "", "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        month_name = months[int(month)]
        return f"The Economist: {month_name} {int(day)}, {year}"


def main() -> int:
    """Main entry point."""
    try:
        sync = EconomistSync()
        sync.sync()
    except KeyboardInterrupt:
        print("\nEconomist sync interrupted.")
    except Exception as e:
        print(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

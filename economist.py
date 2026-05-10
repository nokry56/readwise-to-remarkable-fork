#!/usr/bin/env python3
"""Weekly Economist PDF sync — downloads from GitHub, uploads to reMarkable.

Syncs every edition on the upstream repo that is not yet in the tracker, not
just the most recent one. If a week was missed (container down, upstream API
hiccup, transient upload failure), it gets picked up next cycle instead of
being skipped forever.
"""

import re
import sys
from pathlib import Path

import requests

from config import Config
from tracker import ExportTracker
from uploader import RemarkableUploader

GITHUB_REPO = "evanbio/The_Economist"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/contents"
GITHUB_RAW = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main"

EDITION_PATTERN = re.compile(r"^TE-(\d{4})-(\d{2})-(\d{2})$")


class EconomistSync:
    """Downloads Economist PDFs from GitHub and uploads to reMarkable."""

    def __init__(self, config_path: Path | None = None) -> None:
        self.config = Config(config_path)
        self.tracker = ExportTracker()
        self.temp_dir = Path(__file__).parent / "temp"
        self.temp_dir.mkdir(exist_ok=True)
        self.uploader = RemarkableUploader(
            self.config.rmapi_path,
            self.config.economist_folder,
        )

    def sync(self) -> None:
        """Sync every Economist edition not already tracked."""
        if not self.config.economist_enabled:
            return

        print("\n--- Economist sync ---")
        try:
            editions = self._list_editions()
            if not editions:
                print("Could not list Economist editions from GitHub.")
                return

            unsynced = [
                e for e in editions
                if not self.tracker.is_economist_synced(e["name"])
            ]
            if not unsynced:
                print(f"Up to date ({len(editions)} editions, latest {editions[-1]['name']}).")
                return

            print(f"Found {len(unsynced)} unsynced edition(s); processing oldest first.")
            for edition in unsynced:
                self._sync_one(edition["name"])
        except Exception as e:
            print(f"Economist sync failed: {e}")
        finally:
            self._cleanup()

    def _sync_one(self, edition_id: str) -> None:
        """Download and upload a single edition. Tracker only marked on success."""
        print(f"\nSyncing {edition_id}...")
        pdf_url = self._get_pdf_url(edition_id)
        if not pdf_url:
            print(f"  No PDF found for {edition_id}, skipping.")
            return

        title = self._format_title(edition_id)

        pdf_path = self._download_pdf(edition_id, pdf_url)
        if not pdf_path:
            return

        titled_path = self.temp_dir / f"{title}.pdf"
        if titled_path.exists():
            titled_path.unlink()
        pdf_path.rename(titled_path)

        if self.uploader.upload_file(titled_path):
            self.tracker.mark_economist_synced(edition_id, title)
            print(f"  Uploaded to reMarkable: {title}")
        else:
            print(f"  Failed to upload {title} — will retry next cycle.")

    def _list_editions(self) -> list[dict]:
        """Return all editions on the repo, sorted ascending by date."""
        try:
            response = requests.get(GITHUB_API, timeout=30)
            response.raise_for_status()
            contents = response.json()

            editions = [
                item
                for item in contents
                if item.get("type") == "dir" and EDITION_PATTERN.match(item.get("name", ""))
            ]
            editions.sort(key=lambda x: x["name"])
            return editions
        except Exception as e:
            print(f"Failed to list GitHub repo contents: {e}")
            return []

    def _get_pdf_url(self, edition_id: str) -> str | None:
        try:
            response = requests.get(f"{GITHUB_API}/{edition_id}", timeout=30)
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
            print(f"  Failed to list edition contents for {edition_id}: {e}")
            return None

    def _download_pdf(self, edition_id: str, url: str) -> Path | None:
        pdf_path = self.temp_dir / f"{edition_id}.pdf"
        try:
            print(f"  Downloading {edition_id}.pdf...")
            response = requests.get(url, timeout=300, stream=True)
            response.raise_for_status()

            with pdf_path.open("wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            size_mb = pdf_path.stat().st_size / (1024 * 1024)
            print(f"  Downloaded {size_mb:.1f} MB")
            return pdf_path

        except Exception as e:
            print(f"  Failed to download {edition_id}: {e}")
            return None

    def _cleanup(self) -> None:
        try:
            for f in self.temp_dir.glob("*.pdf"):
                if "Economist" in f.name or f.name.startswith("TE-"):
                    f.unlink()
        except Exception:
            pass

    @staticmethod
    def _format_title(edition_id: str) -> str:
        """TE-2026-04-04 → 'The Economist: April 4, 2026'"""
        match = EDITION_PATTERN.match(edition_id)
        if not match:
            return f"The Economist: {edition_id}"

        year, month, day = match.groups()
        months = [
            "", "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        return f"The Economist: {months[int(month)]} {int(day)}, {year}"


def main() -> int:
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

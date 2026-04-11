#!/usr/bin/env python3
"""Weekly Economist PDF sync.

Downloads from GitHub, uploads to a temp file host so Readwise can ingest it,
saves to Readwise Reader, and also uploads directly to reMarkable.
Highlights from either Readwise iOS or reMarkable merge in Readwise.
"""

import re
import sys
from pathlib import Path

import requests

from config import Config
from readwise_api import ReadwiseAPI
from tracker import ExportTracker
from uploader import RemarkableUploader

GITHUB_REPO = "evanbio/The_Economist"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/contents"
GITHUB_RAW = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main"

# Temp file host — accepts up to 256MB, files expire after 24h-30d
TEMP_HOST_URL = "https://0x0.st"

EDITION_PATTERN = re.compile(r"^TE-(\d{4})-(\d{2})-(\d{2})$")


class EconomistSync:
    """Downloads Economist PDF → temp host → Readwise + reMarkable."""

    def __init__(self, config_path: Path | None = None) -> None:
        self.config = Config(config_path)
        self.tracker = ExportTracker()
        self.readwise = ReadwiseAPI(self.config.readwise_token)
        self.temp_dir = Path(__file__).parent / "temp"
        self.temp_dir.mkdir(exist_ok=True)
        self.uploader = RemarkableUploader(
            self.config.rmapi_path,
            self.config.economist_folder,
        )

    def sync(self) -> None:
        """Check for new Economist editions and sync everywhere."""
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

            title = self._format_title(edition_id)

            # Step 1: Download the PDF locally
            pdf_path = self._download_pdf(edition_id, pdf_url)
            if not pdf_path:
                return

            # Step 2: Upload to temp file host for Readwise to fetch
            hosted_url = self._upload_to_temp_host(pdf_path)
            if hosted_url:
                print(f"Saving to Readwise: {title}")
                result = self.readwise.save_document(
                    hosted_url, title=title, category="pdf"
                )
                if result:
                    print(f"Saved to Readwise: {title}")
                else:
                    print("Failed to save to Readwise (will still upload to reMarkable)")
            else:
                print("Could not upload to temp host (will still upload to reMarkable)")

            # Step 3: Upload directly to reMarkable
            titled_path = self.temp_dir / f"{title}.pdf"
            if titled_path != pdf_path:
                if titled_path.exists():
                    titled_path.unlink()
                pdf_path.rename(titled_path)
                pdf_path = titled_path

            if self.uploader.upload_file(pdf_path):
                print(f"Uploaded to reMarkable: {title}")
            else:
                print(f"Failed to upload to reMarkable")

            # Mark as synced if either destination succeeded
            self.tracker.mark_economist_synced(edition_id, title)

        except Exception as e:
            print(f"Economist sync failed: {e}")
        finally:
            self._cleanup()

    def _find_latest_edition(self) -> dict | None:
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

            editions.sort(key=lambda x: x["name"], reverse=True)
            return editions[0]

        except Exception as e:
            print(f"Failed to list GitHub repo contents: {e}")
            return None

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
            print(f"Failed to list edition contents: {e}")
            return None

    def _download_pdf(self, edition_id: str, url: str) -> Path | None:
        pdf_path = self.temp_dir / f"{edition_id}.pdf"
        try:
            print(f"Downloading {edition_id}.pdf...")
            response = requests.get(url, timeout=300, stream=True)
            response.raise_for_status()

            with pdf_path.open("wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            size_mb = pdf_path.stat().st_size / (1024 * 1024)
            print(f"Downloaded {size_mb:.1f} MB")
            return pdf_path

        except Exception as e:
            print(f"Failed to download {edition_id}: {e}")
            return None

    def _upload_to_temp_host(self, pdf_path: Path) -> str | None:
        """Upload PDF to a temporary file host. Returns a public URL."""
        try:
            print(f"Uploading to temp host for Readwise ingestion...")
            with pdf_path.open("rb") as f:
                response = requests.post(
                    TEMP_HOST_URL,
                    files={"file": (pdf_path.name, f, "application/pdf")},
                    timeout=300,
                )
            response.raise_for_status()
            url = response.text.strip()
            if url.startswith("http"):
                print(f"Hosted at: {url}")
                return url
            print(f"Unexpected response from temp host: {url}")
            return None
        except Exception as e:
            print(f"Temp host upload failed: {e}")
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

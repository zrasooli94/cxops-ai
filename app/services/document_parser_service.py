from io import BytesIO
from pathlib import Path

from pypdf import PdfReader


class UnsupportedDocumentTypeError(Exception):
    pass


class DocumentParserService:
    @staticmethod
    def parse(
        *,
        filename: str,
        content: bytes,
    ) -> str:

        extension = Path(filename).suffix.lower()

        if extension == ".pdf":
            return DocumentParserService._parse_pdf(content)

        if extension in {
            ".txt",
            ".md",
            ".markdown",
        }:
            return content.decode(
                "utf-8",
                errors="ignore",
            )

        raise UnsupportedDocumentTypeError(f"Unsupported file type: {extension}")

    @staticmethod
    def _parse_pdf(
        content: bytes,
    ) -> str:

        reader = PdfReader(BytesIO(content))

        pages: list[str] = []

        for page in reader.pages:
            text = page.extract_text()

            if text:
                pages.append(text.strip())

        return "\n\n".join(pages)

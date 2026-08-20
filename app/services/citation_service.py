import re


class CitationService:
    CITATION_PATTERN = re.compile(r"\[(S\d+)\]")

    @classmethod
    def extract(
        cls,
        text: str,
    ) -> set[str]:

        return set(cls.CITATION_PATTERN.findall(text))

    @classmethod
    def validate(
        cls,
        *,
        answer: str,
        valid_source_ids: set[str],
    ) -> tuple[bool, set[str]]:

        citations = cls.extract(answer)

        if not citations:
            return False, set()

        invalid = citations - valid_source_ids

        if invalid:
            return False, invalid

        return True, set()

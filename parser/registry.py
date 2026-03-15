"""파서 레지스트리 - 채널에 맞는 파서를 자동 선택."""
from parser.base import BaseParser, ParsedReport
from parser.repostory import RepostoryParser
from parser.companyreport import CompanyReportParser
from parser.generic import GenericParser

_PARSERS: list[BaseParser] = [
    RepostoryParser(),
    CompanyReportParser(),
    # SearfinParser(),
    # CbEqResearchParser(),
    GenericParser(),  # fallback (항상 마지막)
]


def parse_message(message_text: str, channel: str, message_id: int | None = None) -> ParsedReport | None:
    """채널에 맞는 파서를 찾아 파싱. 모두 실패하면 None."""
    for parser in _PARSERS:
        if parser.can_parse(channel):
            result = parser.parse(message_text, channel, message_id)
            if result is not None:
                return result
    return None


def parse_messages(message_text: str, channel: str, message_id: int | None = None) -> list[ParsedReport]:
    """메시지를 파싱해 ParsedReport 목록 반환. 다이제스트는 여러 건으로 분리."""
    for parser in _PARSERS:
        if parser.can_parse(channel):
            if hasattr(parser, "parse_multiple"):
                return parser.parse_multiple(message_text, channel, message_id)
            result = parser.parse(message_text, channel, message_id)
            return [result] if result is not None else []
    return []

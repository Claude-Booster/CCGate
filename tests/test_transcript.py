import json
import tempfile
from pathlib import Path

from ccgate.transcript import (
    CapabilityBand, detect_band, encode_cwd,
    grand_total_input, infer_ttl_from_usage, read_transcript, Usage,
)


class TestEncodeCwd:
    def test_windows_drive_colon_becomes_two_hyphens(self):
        result = encode_cwd("C:\\Users\\fred\\project")
        assert result.startswith("c--Users-fred-project")

    def test_windows_dot_in_username(self):
        result = encode_cwd("C:\\Users\\frederick.mandap\\CCGate")
        assert result == "c--Users-frederick-mandap-CCGate"

    def test_windows_space_in_path(self):
        result = encode_cwd("C:\\Users\\fred\\OneDrive - Accenture\\Docs")
        assert result == "c--Users-fred-OneDrive---Accenture-Docs"

    def test_posix_path(self):
        result = encode_cwd("/home/user/projects/ccgate")
        assert result == "-home-user-projects-ccgate"

    def test_known_real_path(self):
        result = encode_cwd("C:\\Users\\frederick.mandap\\OneDrive - Accenture\\Documents\\CCGate")
        assert result == "c--Users-frederick-mandap-OneDrive---Accenture-Documents-CCGate"

    def test_no_trailing_content_lost(self):
        a = encode_cwd("/a/b/c")
        b = encode_cwd("/a/b/cd")
        assert a != b


class TestDetectBand:
    def test_band1_no_prompt_cache(self):
        assert detect_band({}) == CapabilityBand.BAND1_PRE_CACHE
        assert detect_band({"model": {"id": "claude-sonnet-5"}}) == CapabilityBand.BAND1_PRE_CACHE

    def test_band2_prompt_cache_no_miss_causes(self):
        payload = {"prompt_cache": {"warm": True, "hit_ratio": 0.9}}
        assert detect_band(payload) == CapabilityBand.BAND2_CACHE_NO_CAUSES

    def test_band3_full(self):
        payload = {"prompt_cache": {"warm": True, "miss_causes": {}}}
        assert detect_band(payload) == CapabilityBand.BAND3_FULL

    def test_band3_miss_causes_empty_dict_counts(self):
        payload = {"prompt_cache": {"miss_causes": {}}}
        assert detect_band(payload) == CapabilityBand.BAND3_FULL


class TestReadTranscript:
    def _write_jsonl(self, entries: list[dict]) -> Path:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl",
                                        delete=False, encoding="utf-8")
        for e in entries:
            f.write(json.dumps(e) + "\n")
        f.close()
        return Path(f.name)

    def _assistant(self, model: str, inp: int, read: int, create: int,
                   out: int = 5, h1: int = 0, h5: int = 0,
                   ts: str = "2026-09-19T10:00:00.000Z") -> dict:
        return {
            "type": "assistant",
            "timestamp": ts,
            "message": {
                "role": "assistant",
                "model": model,
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": inp,
                    "cache_read_input_tokens": read,
                    "cache_creation_input_tokens": create,
                    "output_tokens": out,
                    "cache_creation": {
                        "ephemeral_1h_input_tokens": h1,
                        "ephemeral_5m_input_tokens": h5,
                    },
                },
            },
        }

    def _user(self, text: str = "hello") -> dict:
        return {"type": "user", "message": {"role": "user", "content": text}}

    def test_reads_assistant_entries_only(self):
        path = self._write_jsonl([
            self._user("hi"),
            self._assistant("claude-sonnet-5", 1000, 0, 1000),
            self._user("next"),
            self._assistant("claude-sonnet-5", 1200, 1000, 200),
        ])
        reqs = read_transcript(path)
        assert len(reqs) == 2
        assert reqs[0].index == 0
        assert reqs[1].index == 1

    def test_usage_fields_parsed(self):
        path = self._write_jsonl([
            self._assistant("claude-opus-5", 5000, 4500, 500, h1=500),
        ])
        reqs = read_transcript(path)
        u = reqs[0].usage
        assert u.input_tokens == 5000
        assert u.cache_read_input_tokens == 4500
        assert u.cache_creation_input_tokens == 500
        assert u.ephemeral_1h_input_tokens == 500

    def test_model_id_extracted(self):
        path = self._write_jsonl([
            self._assistant("claude-opus-5", 1000, 0, 1000),
        ])
        reqs = read_transcript(path)
        assert reqs[0].model_id == "claude-opus-5"

    def test_skips_malformed_lines(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl",
                                        delete=False, encoding="utf-8")
        f.write("not json\n")
        f.write(json.dumps(self._assistant("claude-sonnet-5", 100, 0, 100)) + "\n")
        f.close()
        reqs = read_transcript(Path(f.name))
        assert len(reqs) == 1


class TestInferTtl:
    def _req(self, h1=0, h5=0):
        from ccgate.transcript import Request, Usage
        return Request(0, "", "claude-sonnet-5", Usage(ephemeral_1h_input_tokens=h1,
                                                       ephemeral_5m_input_tokens=h5))

    def test_1h_tokens_means_3600(self):
        assert infer_ttl_from_usage([self._req(h1=1000)]) == 3600

    def test_5m_tokens_means_300(self):
        assert infer_ttl_from_usage([self._req(h5=1000)]) == 300

    def test_no_tokens_defaults_to_300(self):
        assert infer_ttl_from_usage([self._req()]) == 300


def test_grand_total_input_sums_fresh_and_cached():
    u = Usage(input_tokens=100, cache_read_input_tokens=9000,
              cache_creation_input_tokens=500, output_tokens=42)
    assert grand_total_input(u) == 9600


def test_grand_total_input_differs_from_input_tokens_alone():
    # The whole bug class: input_tokens is fresh-only, not the grand total.
    u = Usage(input_tokens=3, cache_read_input_tokens=15000,
              cache_creation_input_tokens=0)
    assert grand_total_input(u) == 15003
    assert grand_total_input(u) != u.input_tokens

"""Telling text encoders apart by their safetensors headers."""

from __future__ import annotations

import json
import struct

import pytest

from backend.services import text_encoder_check as te


def _t5(width=4096, vocab=32128, blocks=24, bias_in_every_block=False):
    tensors = {"shared.weight": [vocab, width], "encoder.final_layer_norm.weight": [width]}
    for i in range(blocks):
        tensors[f"encoder.block.{i}.layer.0.SelfAttention.q.weight"] = [width, width]
        if i == 0 or bias_in_every_block:
            tensors[f"encoder.block.{i}.layer.0.SelfAttention.relative_attention_bias.weight"] = [32, 64]
    return tensors


def _clip(width, layers=12):
    tensors = {"text_model.embeddings.token_embedding.weight": [49408, width]}
    for i in range(layers):
        tensors[f"text_model.encoder.layers.{i}.self_attn.q_proj.weight"] = [width, width]
    return tensors


@pytest.mark.parametrize("tensors, arch", [
    (_t5(), "t5-xxl"),
    # A prefixed copy, as all-in-one files carry it.
    ({f"text_encoders.t5xxl.transformer.{k}": v for k, v in _t5().items()}, "t5-xxl"),
    (_t5(vocab=256384), "umt5-xxl"),
    # UMT5 without its embedding still shows a bias in every block.
    ({k: v for k, v in _t5(bias_in_every_block=True).items() if k != "shared.weight"}, "umt5-xxl"),
    (_t5(width=2048), "t5"),
    (_clip(768), "clip-l"),
    (_clip(1280, layers=32), "clip-g"),
    (_clip(1024, layers=24), "clip-h"),
    ({"model.embed_tokens.weight": [151936, 2560], "model.layers.0.self_attn.q_proj.weight": [4096, 2560]}, "llm"),
    ({"double_blocks.0.img_attn.qkv.weight": [9216, 3072], "img_in.weight": [3072, 64]}, "diffusion"),
    ({"lora_unet_double_blocks_0_img_attn_qkv.lora_up.weight": [9216, 16]}, "lora"),
    ({"something.else": [3]}, None),
    ({}, None),
])
def test_classify(tensors, arch):
    assert te.classify(tensors) == arch


def _write_safetensors(path, tensors):
    header = {name: {"dtype": "F16", "shape": shape, "data_offsets": [0, 0]} for name, shape in tensors.items()}
    header["__metadata__"] = {"format": "pt"}
    raw = json.dumps(header).encode()
    path.write_bytes(struct.pack("<Q", len(raw)) + raw)


def test_local_tensors_reads_only_the_header(tmp_path):
    path = tmp_path / "t5.safetensors"
    _write_safetensors(path, _t5(blocks=2))
    tensors = te.local_tensors(path)
    assert "__metadata__" not in tensors
    assert tensors["shared.weight"] == [32128, 4096]
    assert te.classify(tensors) == "t5-xxl"


def test_local_mismatch_names_both_architectures(tmp_path):
    path = tmp_path / "clip_l.safetensors"
    _write_safetensors(path, _clip(768))
    problem = te.local_mismatch(path, "t5-xxl", "T5-XXL")
    assert "clip_l.safetensors is a CLIP-L" in problem and "reads a T5-XXL" in problem
    assert te.local_mismatch(path, "clip-l", "CLIP-L") is None


def test_an_unreadable_file_is_left_to_the_loader(tmp_path):
    path = tmp_path / "broken.safetensors"
    path.write_bytes(b"not a header")
    assert te.local_mismatch(path, "t5-xxl", "T5-XXL") is None


def test_mismatch_sends_models_and_loras_to_their_own_role():
    assert "Add it as a new generation model" in te.mismatch("diffusion", "t5-xxl", "flux1-dev.safetensors", "T5-XXL")
    assert "Add it as a LoRA" in te.mismatch("lora", "clip-l", "style.safetensors", "CLIP-L")
    assert te.mismatch(None, "t5-xxl", "x.safetensors", "T5-XXL") is None


def test_hf_tensors_only_asks_for_safetensors(monkeypatch):
    called = []

    class _Api:
        def parse_safetensors_file_metadata(self, *a, **k):
            called.append(a)
            raise AssertionError("must not be asked")

    monkeypatch.setattr("huggingface_hub.HfApi", _Api)
    assert te.hf_tensors("org/repo", "t5xxl.gguf") is None
    assert called == []


def test_hf_tensors_returns_none_when_the_header_cannot_be_fetched(monkeypatch):
    class _Api:
        def parse_safetensors_file_metadata(self, *a, **k):
            raise OSError("gated")

    monkeypatch.setattr("huggingface_hub.HfApi", _Api)
    assert te.hf_tensors("org/repo", "t5.safetensors") is None

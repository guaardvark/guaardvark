"""The trainer renders every row with the base model's own chat template,
refuses chat rows for a model without one, builds SFTConfig for the TRL that
is installed, hands the tokenizer over as processing_class, picks bf16 only
where the GPU has it, and takes the loss on replies only for all-chat data.

Seams: unsloth, trl, transformers and datasets are imported by finetune() at
call time, so sys.modules stand-ins reach them; torch is real with CUDA
reported absent, so nothing touches the GPU, and the trainer's own GPU check
(require_cuda) is stood in alongside them."""
import dataclasses
import importlib.util
import inspect
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "services" / "training" / "scripts" / "finetune_model.py"


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("_finetune_model_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ChatTokenizer:
    """ChatML like Qwen2.5's template; tokens are whitespace-separated words."""

    chat_template = "{% for m in messages %}...{% endfor %}"

    def __init__(self):
        self.saved = []

    def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=False):
        assert tokenize is False
        return "".join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in messages)

    def __call__(self, text, truncation=False, add_special_tokens=True, **kwargs):
        return {"input_ids": text.split()}

    def save_pretrained(self, path):
        Path(path).mkdir(parents=True, exist_ok=True)
        self.saved.append(path)


class NoTemplateTokenizer(ChatTokenizer):
    chat_template = None


ROWS = [
    {"instruction": "Name a colour.", "output": "Blue."},
    {"messages": [{"role": "user", "content": "Hi?"}, {"role": "assistant", "content": "Hello."}]},
    {"conversations": [{"from": "human", "value": "2+2?"}, {"from": "gpt", "value": "4."}]},
    {"prompt": "Capital of France?", "completion": "Paris."},
]


def test_chat_rows_are_rendered_with_the_model_template_and_never_with_empty_prompts(script):
    texts, report = script.render_rows(ROWS, ChatTokenizer())

    assert texts[1] == "<|im_start|>user\nHi?<|im_end|>\n<|im_start|>assistant\nHello.<|im_end|>\n"
    assert texts[0].startswith("<|im_start|>user\nName a colour.")
    assert all("<|im_start|>user\n<|im_end|>" not in t for t in texts)
    assert report == {"rows": 4, "used": 4, "skipped": 0, "text_rows": 0, "chat_rows": 4}


def test_plain_text_rows_are_used_as_written_and_unusable_rows_are_skipped(script):
    rows = [{"text": "Plain words."}, {"text": "  "}, {"messages": [{"role": "user", "content": "No answer"}]}]
    texts, report = script.render_rows(rows, ChatTokenizer())
    assert texts == ["Plain words."]
    assert report["skipped"] == 2 and report["text_rows"] == 1


def test_a_model_without_a_chat_template_is_refused_for_chat_rows_only(script):
    with pytest.raises(ValueError, match="no chat template"):
        script.render_rows(ROWS, NoTemplateTokenizer())
    texts, _ = script.render_rows([{"text": "Plain words."}], NoTemplateTokenizer())
    assert texts == ["Plain words."]


def test_load_training_data_skips_blank_and_broken_lines(script, tmp_path, monkeypatch):
    datasets = types.ModuleType("datasets")
    datasets.Dataset = SimpleNamespace(from_list=lambda rows: rows)
    monkeypatch.setitem(sys.modules, "datasets", datasets)
    data = tmp_path / "d.jsonl"
    data.write_text(json.dumps(ROWS[0]) + "\n\n{broken\n" + json.dumps(ROWS[1]) + "\n")

    rows, report = script.load_training_data(str(data), ChatTokenizer())

    assert len(rows) == 2 and all(set(r) == {"text"} for r in rows)
    assert report["unreadable"] == 1 and report["used"] == 2


@pytest.mark.parametrize("name", ["max_length", "max_seq_length"])
def test_sft_config_names_the_length_the_way_the_installed_trl_does(script, tmp_path, name):
    fields = {"per_device_train_batch_size", "max_steps", "learning_rate", "bf16", "fp16",
              "output_dir", "report_to", "dataset_text_field", name}
    kwargs = script.sft_config_kwargs(fields, output_dir=tmp_path, max_steps=40, learning_rate=1e-4,
                                      batch_size=2, gradient_accumulation_steps=4, max_seq_length=1024,
                                      bf16=False)
    assert kwargs[name] == 1024
    assert set(kwargs) <= fields
    assert kwargs["report_to"] == "none"
    assert kwargs["bf16"] is False and kwargs["fp16"] is True


def test_finetune_takes_no_cpu_offload_switch(script):
    assert not any("offload" in p for p in inspect.signature(script.finetune).parameters)
    assert "deepspeed" not in SCRIPT.read_text().lower()


# ---- one run through finetune() with the libraries stood in ---------------------

@pytest.fixture
def libraries(monkeypatch, script):
    # The stand-ins below replace Unsloth, so its GPU precondition is stood in too.
    monkeypatch.setattr(script, "require_cuda", lambda: None)
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    state = SimpleNamespace(loaded=None, trainers=[], responses_only=[], bf16=True)

    class Model:
        training = False

        def save_pretrained(self, path):
            Path(path).mkdir(parents=True, exist_ok=True)

    tokenizer = ChatTokenizer()

    def from_pretrained(**kwargs):
        state.loaded = kwargs
        return Model(), tokenizer

    unsloth = types.ModuleType("unsloth")
    unsloth.FastLanguageModel = SimpleNamespace(from_pretrained=from_pretrained,
                                                get_peft_model=lambda model, **k: model)
    unsloth.is_bf16_supported = lambda: state.bf16
    chat_templates = types.ModuleType("unsloth.chat_templates")

    def train_on_responses_only(trainer, instruction_part, response_part):
        state.responses_only.append((instruction_part, response_part))
        return trainer

    chat_templates.train_on_responses_only = train_on_responses_only

    SFTConfig = dataclasses.make_dataclass("SFTConfig", [
        (f, object, None) for f in ("per_device_train_batch_size", "gradient_accumulation_steps",
                                    "warmup_steps", "max_steps", "learning_rate", "bf16", "fp16",
                                    "logging_steps", "output_dir", "optim", "save_steps",
                                    "save_total_limit", "report_to", "seed", "dataset_text_field",
                                    "max_length")])

    class SFTTrainer:
        def __init__(self, model=None, args=None, train_dataset=None, processing_class=None, callbacks=None):
            self.kwargs = {"args": args, "train_dataset": train_dataset,
                           "processing_class": processing_class, "callbacks": callbacks}
            self.train_calls = []
            state.trainers.append(self)

        def train(self, **kwargs):
            self.train_calls.append(kwargs)

    trl = types.ModuleType("trl")
    trl.SFTConfig, trl.SFTTrainer = SFTConfig, SFTTrainer

    class Dataset(list):
        @classmethod
        def from_list(cls, rows):
            return cls(rows)

        def filter(self, fn):
            return Dataset(r for r in self if fn(r))

        def __getitem__(self, key):
            if key == "text":
                return [r["text"] for r in self]
            return list.__getitem__(self, key)

    datasets = types.ModuleType("datasets")
    datasets.Dataset = Dataset
    for name, module in (("unsloth", unsloth), ("unsloth.chat_templates", chat_templates),
                         ("trl", trl), ("datasets", datasets)):
        monkeypatch.setitem(sys.modules, name, module)
    state.tokenizer = tokenizer
    return state


MARKERS = {"instruction": "<|im_start|>user\n", "response": "<|im_start|>assistant\n"}


def _data(tmp_path, rows):
    path = tmp_path / "train.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(path)


def test_a_run_passes_the_tokenizer_as_processing_class_and_takes_loss_on_replies(script, libraries, tmp_path):
    reports = []
    out = script.finetune("/models/qwen", _data(tmp_path, ROWS), output_dir=str(tmp_path / "out"),
                          max_steps=30, max_seq_length=64, load_in_4bit=False, response_markers=MARKERS,
                          dataset_callback=reports.append)

    assert out == str(tmp_path / "out")
    assert libraries.loaded["model_name"] == "/models/qwen"
    assert libraries.loaded["load_in_4bit"] is False
    (trainer,) = libraries.trainers
    assert trainer.kwargs["processing_class"] is libraries.tokenizer
    config = trainer.kwargs["args"]
    assert config.max_length == 64 and config.max_steps == 30
    assert config.bf16 is True and config.fp16 is False
    assert config.report_to == "none"
    assert len(trainer.kwargs["train_dataset"]) == 4
    assert libraries.responses_only == [(MARKERS["instruction"], MARKERS["response"])]
    assert reports[0]["loss_on"] == "assistant replies" and reports[0]["trained"] == 4
    assert (tmp_path / "out" / "lora").is_dir()


def test_without_bf16_the_run_uses_fp16_and_plain_text_rows_keep_whole_text_loss(script, libraries, tmp_path):
    libraries.bf16 = False
    reports = []
    script.finetune("/models/qwen", _data(tmp_path, ROWS + [{"text": "Plain words."}]),
                    output_dir=str(tmp_path / "out"), max_steps=30, max_seq_length=64,
                    response_markers=MARKERS, dataset_callback=reports.append)

    config = libraries.trainers[0].kwargs["args"]
    assert config.bf16 is False and config.fp16 is True
    assert libraries.responses_only == []
    assert reports[0]["loss_on"] == "whole text"


def test_rows_longer_than_the_sequence_limit_are_left_out_and_counted(script, libraries, tmp_path):
    long = {"instruction": "Say a lot.", "output": " ".join(["word"] * 200)}
    reports = []
    script.finetune("/models/qwen", _data(tmp_path, ROWS + [long]), output_dir=str(tmp_path / "out"),
                    max_steps=30, max_seq_length=64, dataset_callback=reports.append)
    assert reports[0]["too_long"] == 1 and reports[0]["trained"] == 4


def test_check_report_names_the_trl_interface_the_trainer_will_use(script, libraries, monkeypatch):
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "get_device_name", lambda i: "test card")
    monkeypatch.setattr(torch.cuda, "is_bf16_supported", lambda: True)

    report = script.check_report()

    assert report["ok"] is True, report["problems"]
    assert report["cuda_device"] == "test card" and report["bf16"] is True
    assert "max_length" in report["sft_config_fields"]
    assert report["sft_trainer_params"] == ["processing_class"]
    assert report["train_on_responses_only"] is True
    json.dumps(report)


def test_check_report_is_not_ok_without_the_libraries(script, monkeypatch):
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    for name in ("unsloth", "unsloth.chat_templates", "trl", "datasets"):
        monkeypatch.setitem(sys.modules, name, None)

    report = script.check_report()

    assert report["ok"] is False
    assert any(p.startswith("unsloth:") for p in report["problems"])
    assert any(p.startswith("trl:") for p in report["problems"])
    assert "CUDA is not available to the trainer" in report["problems"]


def test_check_report_never_imports_unsloth_without_a_gpu(script, monkeypatch):
    # Without a usable GPU, importing Unsloth probes download.pytorch.org for a ROCm index.
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    touched = []

    class Watched(types.ModuleType):
        def __getattr__(self, name):
            touched.append(f"{self.__name__}.{name}")
            raise AttributeError(name)

    for name in ("unsloth", "unsloth.chat_templates"):
        monkeypatch.setitem(sys.modules, name, Watched(name))

    report = script.check_report()

    assert touched == []
    assert report["train_on_responses_only"] is False
    assert "CUDA is not available to the trainer" in report["problems"]


def test_merge_folds_the_adapter_into_a_bf16_base_offline(script, tmp_path, monkeypatch):
    import torch

    seen = {}

    class Merged:
        def save_pretrained(self, path, safe_serialization=False):
            seen["saved"] = (path, safe_serialization)

    class Peft:
        def merge_and_unload(self):
            return Merged()

    class Tokenizer:
        def save_pretrained(self, path):
            seen["tokenizer_saved"] = path

    transformers = types.ModuleType("transformers")
    transformers.AutoModelForCausalLM = SimpleNamespace(
        from_pretrained=lambda path, **kw: seen.update(base=(path, kw)) or "base")
    transformers.AutoTokenizer = SimpleNamespace(
        from_pretrained=lambda path, **kw: seen.update(tokenizer=(path, kw)) or Tokenizer())
    peft = types.ModuleType("peft")
    peft.PeftModel = SimpleNamespace(from_pretrained=lambda base, path: seen.update(adapter=(base, path)) or Peft())
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setitem(sys.modules, "peft", peft)

    out = script.merge_adapter("/models/qwen", "/run/lora", str(tmp_path / "merged"))

    assert out == str(tmp_path / "merged")
    assert seen["base"] == ("/models/qwen", {"dtype": torch.bfloat16, "local_files_only": True})
    assert seen["adapter"] == ("base", "/run/lora")
    assert seen["saved"] == (out, True)
    assert seen["tokenizer"] == ("/run/lora", {"local_files_only": True})
    assert seen["tokenizer_saved"] == out


"""Completed-game supervision, battle-disjoint validation, resumable checkpoints."""

import hashlib
import json
from pathlib import Path
import random
import time

import torch
from torch.nn import functional as F

from .model import (
    PolicyValueNet,
    ModelConfig,
    checkpoint_payload,
    device_for,
    load_checkpoint,
)
from .observation import encode


def load_games(path, split):
    games = [
        json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()
    ]
    if not games or any(g["split"] != split for g in games):
        raise ValueError(f"Expected nonempty {split} trajectories")
    if len({g["id"] for g in games}) != len(games):
        raise ValueError("Duplicate battles")
    completed = [g for g in games if g["termination"] == "terminal"]
    if not completed:
        raise ValueError(
            "No completed games; censored games cannot supervise terminal values"
        )
    return games, completed


def check_separation(train, validation):
    for key in ("id",):
        if {g[key] for g in train} & {g[key] for g in validation}:
            raise ValueError("Battle leakage")
    for key in ("teams", "sets"):
        if {x for g in train for x in g[key]} & {x for g in validation for x in g[key]}:
            raise ValueError(f"Train/validation {key} leakage")
    if len({g["manifest"] for g in train + validation}) != 1:
        raise ValueError("Different team manifests")


def rows(games):
    return [r for g in games for r in g["records"] if r["outcome"] is not None]


@torch.inference_mode()
def value_metrics(model, examples, device, batch_size):
    model.eval()
    values, targets = [], []
    for offset in range(0, len(examples), batch_size):
        batch_rows = examples[offset : offset + batch_size]
        batch = {
            k: v.to(device)
            for k, v in encode([r["observation"] for r in batch_rows]).items()
        }
        _, logits = model(batch)
        values.extend(logits.sigmoid().cpu().tolist())
        targets.extend(r["outcome"] for r in batch_rows)
    p = torch.tensor(values).clamp(1e-7, 1 - 1e-7)
    y = torch.tensor(targets)
    bins = []
    ece = 0.0
    for i in range(10):
        select = (p >= i / 10) & (p < (i + 1) / 10)
        n = int(select.sum())
        if n:
            pred, actual = float(p[select].mean()), float(y[select].mean())
            ece += n / len(p) * abs(pred - actual)
            bins.append({"bin": i, "n": n, "prediction": pred, "outcome": actual})
    return {
        "positions": len(p),
        "brier": float(((p - y) ** 2).mean()),
        "log_loss": float(F.binary_cross_entropy(p, y)),
        "accuracy": float(((p >= 0.5) == (y >= 0.5)).float().mean()),
        "ece": ece,
        "calibration": bins,
    }


def train(
    train_path,
    validation_path,
    output,
    *,
    epochs=5,
    batch_size=128,
    lr=0.001,
    device="auto",
    seed=0,
    hidden=128,
    embedding=12,
    policy_weight=0.0,
    resume=None,
):
    if epochs < 1 or batch_size < 1 or lr <= 0 or policy_weight < 0:
        raise ValueError("Invalid training configuration")
    torch.manual_seed(seed)
    random.seed(seed)
    torch.set_num_threads(1)
    dev = device_for(device)
    all_train, train_games = load_games(train_path, "train")
    all_val, val_games = load_games(validation_path, "validation")
    check_separation(all_train, all_val)
    examples, validation = rows(train_games), rows(val_games)
    model = PolicyValueNet(ModelConfig(embedding=embedding, hidden=hidden)).to(dev)
    previous = None
    if resume:
        evaluator, previous = load_checkpoint(resume, str(dev), batch_size)
        if previous["manifest"] != all_train[0]["manifest"]:
            raise ValueError("Resume checkpoint team manifest mismatch")
        model = evaluator.model
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
    if previous:
        optimizer.load_state_dict(previous["optimizer"])
        for state in optimizer.state.values():
            for key, value in state.items():
                if isinstance(value, torch.Tensor):
                    state[key] = value.to(dev)
    history = []
    initial = value_metrics(model, validation, dev, batch_size)
    start = time.perf_counter()
    generator = torch.Generator().manual_seed(seed)
    for epoch in range(epochs):
        model.train()
        order = torch.randperm(len(examples), generator=generator).tolist()
        total_loss = total_value = total_policy = 0.0
        for offset in range(0, len(order), batch_size):
            records = [examples[i] for i in order[offset : offset + batch_size]]
            batch = {
                k: v.to(dev)
                for k, v in encode([r["observation"] for r in records]).items()
            }
            outcomes = torch.tensor(
                [r["outcome"] for r in records], dtype=torch.float32, device=dev
            )
            policy_target = torch.tensor(
                [r["policy_target"] for r in records], dtype=torch.float32, device=dev
            )
            if torch.any(policy_target.masked_select(~batch["legal"]) != 0):
                raise ValueError("Illegal policy target")
            logits, value = model(batch)
            value_loss = F.binary_cross_entropy_with_logits(value, outcomes)
            policy_loss = -(policy_target * logits.log_softmax(-1)).sum(-1).mean()
            loss = value_loss + policy_weight * policy_loss
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += float(loss.detach()) * len(records)
            total_value += float(value_loss.detach()) * len(records)
            total_policy += float(policy_loss.detach()) * len(records)
        history.append(
            {
                "epoch": epoch + 1,
                "loss": total_loss / len(examples),
                "value_loss": total_value / len(examples),
                "policy_loss": total_policy / len(examples),
                "validation": value_metrics(model, validation, dev, batch_size),
            }
        )
    report = {
        "device": str(dev),
        "dtype": "float32",
        "seed": seed,
        "train_battles": len(train_games),
        "validation_battles": len(val_games),
        "censored_train": len(all_train) - len(train_games),
        "train_positions": len(examples),
        "policy_weight": policy_weight,
        "seconds": time.perf_counter() - start,
        "initial_validation": initial,
        "epochs": history,
        "scope": "Native search-model games; no claim of Showdown strength or calibrated probabilities",
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = checkpoint_payload(
        model,
        optimizer=optimizer.state_dict(),
        report=report,
        manifest=all_train[0]["manifest"],
        training_battles=sorted(
            {g["id"] for g in all_train}
            | set(previous.get("training_battles", []) if previous else [])
        ),
        data_sha256=hashlib.sha256(Path(train_path).read_bytes()).hexdigest(),
        parent_sha256=hashlib.sha256(Path(resume).read_bytes()).hexdigest()
        if resume
        else None,
        torch_version=str(torch.__version__),
        rng_state=torch.get_rng_state(),
    )
    temporary = output.with_suffix(".tmp")
    torch.save(payload, temporary)
    temporary.replace(output)
    output.with_suffix(".metrics.json").write_text(json.dumps(report, indent=2) + "\n")
    return report

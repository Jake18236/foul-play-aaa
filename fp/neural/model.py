"""Small float32 policy/value network and batched inference service."""

from dataclasses import asdict, dataclass
import time
import torch
from torch import nn

from .observation import ACTION_COUNT, CATEGORIES, NUMBERS, encode, SCHEMA


@dataclass
class ModelConfig:
    embedding: int = 12
    hidden: int = 128
    entity: int = 48


def device_for(requested="auto"):
    if requested == "auto":
        return torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    if requested not in ("cpu", "mps"):
        raise ValueError("Use auto, cpu, or mps; training uses float32")
    if requested == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("MPS was requested but is unavailable")
    return torch.device(requested)


class PolicyValueNet(nn.Module):
    def __init__(self, config=None):
        super().__init__()
        self.config = config or ModelConfig()
        c = self.config
        self.embedding = nn.Embedding(8192, c.embedding, padding_idx=0)
        self.entity = nn.Sequential(
            nn.Linear(len(CATEGORIES) * c.embedding + len(NUMBERS), c.entity), nn.ReLU()
        )
        self.body = nn.Sequential(
            nn.Linear(12 * c.entity + c.embedding, c.hidden),
            nn.ReLU(),
            nn.Linear(c.hidden, c.hidden),
            nn.ReLU(),
        )
        self.policy = nn.Linear(c.hidden, ACTION_COUNT)
        self.value = nn.Linear(c.hidden, 1)
        self.float()

    def forward(self, batch):
        entity = self.embedding(batch["categorical"]).flatten(2)
        entity = self.entity(torch.cat((entity, batch["numeric"]), dim=-1)).flatten(1)
        effects = (
            self.embedding(batch["effects"]) * batch["effect_values"].unsqueeze(-1)
        ).sum(1) / 16
        hidden = self.body(torch.cat((entity, effects), dim=-1))
        logits = self.policy(hidden).masked_fill(~batch["legal"], -1e9)
        return logits, self.value(hidden).squeeze(-1)


def synchronize(device):
    if device.type == "mps":
        torch.mps.synchronize()


class Evaluator:
    """One PyTorch invocation per batch, never one callback per MCTS node."""

    def __init__(self, model, device="auto", batch_size=256):
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.device = device_for(device)
        self.model = model.to(self.device).float().eval()
        self.batch_size = batch_size
        self.calls = self.positions = 0
        self.seconds = 0.0

    @torch.inference_mode()
    def evaluate(self, observations):
        policies, values = [], []
        synchronize(self.device)
        start = time.perf_counter()
        for offset in range(0, len(observations), self.batch_size):
            batch = {
                k: v.to(self.device)
                for k, v in encode(
                    observations[offset : offset + self.batch_size]
                ).items()
            }
            logits, value = self.model(batch)
            policies.extend(logits.softmax(-1).cpu().tolist())
            values.extend(value.sigmoid().cpu().tolist())
            self.calls += 1
        synchronize(self.device)
        self.seconds += time.perf_counter() - start
        self.positions += len(observations)
        return policies, values


def load_checkpoint(path, device="auto", batch_size=256):
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    if checkpoint["schema"] != SCHEMA:
        raise ValueError("Checkpoint observation schema mismatch")
    model = PolicyValueNet(ModelConfig(**checkpoint["model_config"]))
    model.load_state_dict(checkpoint["model"])
    return Evaluator(model, device, batch_size), checkpoint


def checkpoint_payload(model, **metadata):
    return {
        "schema": SCHEMA,
        "model_config": asdict(model.config),
        "model": {k: v.detach().cpu() for k, v in model.state_dict().items()},
        **metadata,
    }

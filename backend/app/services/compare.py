"""Compare Mode — several models answer the same prompt, side by side.

Runs models concurrently (local GPUs queue internally; each call is
guarded and metered). Supports selecting the best answer and combining
answers into one response via the orchestrator model. Results persist to
``compare_runs`` / ``compare_answers``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from ..core.errors import BadRequest, NotFound
from ..db.repo import CompareRepo
from ..providers.base import ChatMessage
from .model_runner import ModelRunner, UsageSink
from .settings_service import SettingsService

log = logging.getLogger("aicc.compare")

MAX_MODELS = 6


class CompareService:
    def __init__(self, *, runner: ModelRunner, compare: CompareRepo,
                 settings: SettingsService):
        self.runner = runner
        self.compare = compare
        self.settings = settings

    async def run(self, *, prompt: str, model_names: list[str],
                  provider_name: str | None = "ollama",
                  project_id: int | None = None,
                  on_delta=None) -> AsyncIterator[dict[str, Any]]:
        prompt = prompt.strip()
        if not prompt:
            raise BadRequest("Prompt must not be empty.")
        models = [m for m in dict.fromkeys(model_names) if m]
        if not models or len(models) > MAX_MODELS:
            raise BadRequest(f"Select 1–{MAX_MODELS} models to compare.",
                             code="INVALID_COMPARE_SIZE")
        run = await self.compare.create(prompt, project_id)
        run_id = run["id"]
        yield {"type": "run", "run_id": run_id, "models": models}

        async def one(name: str) -> dict[str, Any]:
            sink = UsageSink(conversation_id=None)
            root = {"type": "delta", "model": name}

            async def delta(text: str) -> None:
                if on_delta:
                    await on_delta(root | {"content": text})

            gen = await self.runner.generate(
                messages=[ChatMessage(role="user", content=prompt)],
                provider_name=provider_name, model_name=name, sink=sink,
                on_delta=delta)
            answer = await self.compare.add_answer(
                run_id, name, provider_name or "ollama", gen.text,
                gen.input_tokens or 0, gen.output_tokens or 0, gen.token_method,
                gen.cost_eur, status="error" if gen.status == "error" else "complete",
                error=gen.error)
            return {"model": name, "gen": gen, "answer_id": answer["id"]}

        results = await asyncio.gather(*(one(m) for m in models), return_exceptions=True)
        for m, res in zip(models, results):
            if isinstance(res, BaseException):
                log.warning("compare answer failed for %s: %s", m, res)
                await self.compare.add_answer(
                    run_id, m, provider_name or "ollama", "",
                    0, 0, "estimated", 0.0, status="error",
                    error=getattr(res, "message", str(res)))
                yield {"type": "answer_done", "model": m, "status": "error",
                       "error": getattr(res, "message", str(res))}
            else:
                yield {"type": "answer_done", "model": m, "status": "complete",
                       "answer_id": res["answer_id"],
                       "input_tokens": res["gen"].input_tokens,
                       "output_tokens": res["gen"].output_tokens,
                       "token_method": res["gen"].token_method,
                       "tokens_per_second": res["gen"].tokens_per_second,
                       "cost_eur": res["gen"].cost_eur}
        await self.compare.finish(run_id, status="complete")
        yield {"type": "done", "run_id": run_id, "status": "complete"}

    async def state(self, run_id: int) -> dict:
        run = await self.compare.get(run_id)
        if run is None:
            raise NotFound(f"Compare run '{run_id}' not found.")
        answers = await self.compare.answers(run_id)
        return {"run": run, "answers": answers}

    async def select(self, run_id: int, answer_id: int) -> dict:
        run = await self.compare.get(run_id)
        if run is None:
            raise NotFound(f"Compare run '{run_id}' not found.")
        answers = await self.compare.answers(run_id)
        if not any(a["id"] == answer_id for a in answers):
            raise NotFound(f"Answer '{answer_id}' not found in this run.")
        await self.compare.select(run_id, answer_id)
        selected = next(a for a in await self.compare.answers(run_id) if a["selected"])
        await self.compare.finish(run_id, selected=selected["model"])
        return {"run_id": run_id, "selected_model": selected["model"]}

    async def combine(self, run_id: int, provider_name: str | None = "ollama",
                      model_name: str | None = None) -> dict:
        run = await self.compare.get(run_id)
        if run is None:
            raise NotFound(f"Compare run '{run_id}' not found.")
        answers = [a for a in await self.compare.answers(run_id)
                   if a["status"] == "complete"]
        if not answers:
            raise BadRequest("No complete answers to combine.", code="NO_ANSWERS")
        # prefer the selected answer's model for synthesis
        chosen = next((a for a in answers if a["selected"]), answers[0])
        model_name = model_name or chosen["model"]
        prompt = ("Combine the following answers to one improved, coherent answer "
                  "(keep the best facts, resolve contradictions, no new claims):\n\n" +
                  "\n\n".join(f"--- {a['model']} ---\n{a['answer']}" for a in answers))
        gen = await self.runner.generate(
            messages=[ChatMessage(role="user", content=prompt)],
            provider_name=provider_name, model_name=model_name)
        if gen.status == "error":
            raise BadRequest(f"Combine failed: {gen.error}", code="MODEL_ERROR")
        await self.compare.finish(run_id, combined=gen.text, status="complete")
        return {"run_id": run_id, "combined": gen.text,
                "model": model_name, "tokens": gen.total_tokens}

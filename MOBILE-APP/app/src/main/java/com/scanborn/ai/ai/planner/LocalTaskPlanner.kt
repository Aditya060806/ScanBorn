package com.scanborn.ai.ai.planner

import android.util.Log
import com.scanborn.ai.ai.grammar.Grammars
import com.scanborn.ai.ai.repository.AIRepository
import com.scanborn.ai.model.ChatMessage
import kotlinx.coroutines.flow.toList

/**
 * Natural language -> task plan, entirely on the phone.
 *
 * This is the on-device counterpart of `sarvam/task_engine/`. The prompt wording is copied
 * verbatim from `groq_provider.PROMPT` on purpose: the distillation dataset is generated
 * against that wording, so any drift here would mean the few-shot examples no longer match
 * what the phone actually asks. `tests/test_grammar_parity.py` diffs the two.
 *
 * The grammar does the work that a bigger model would otherwise have to. These tasks are
 * classification and extraction over short fixed lists rather than open generation, so what
 * a 1.5B model needs is not more parameters but an inability to emit anything invalid.
 */
class LocalTaskPlanner(private val repository: AIRepository) {

    companion object {
        private const val TAG = "LocalTaskPlanner"

        /** Verbatim from `sarvam/task_engine/groq_provider.py:PROMPT`. */
        fun prompt(actions: List<String>, targets: List<String>): String =
            "Convert the instruction into a JSON task graph for a mobile robot.\n" +
                "Use only these actions: ${actions.joinToString(", ")}.\n" +
                "Targets must come from this list: ${targets.joinToString(", ")}.\n" +
                "Use an empty target for actions that do not need one.\n" +
                "Reply with JSON only, no prose: " +
                """{"nodes": [{"action": "...", "target": "..."}]}"""
    }

    /**
     * Plan [instruction] against the objects this scan actually found.
     *
     * @param sceneObjects labels from the segmenter. These outrank the ontology, so a
     *                     "sousaphone" this room contains becomes a legal target.
     * @return a plan, empty if the model produced nothing usable. Never throws: a phone that
     *         cannot plan should say so in the UI, not crash the screen that asked.
     */
    suspend fun plan(instruction: String, sceneObjects: List<String> = emptyList()): TaskPlan {
        if (instruction.isBlank()) return TaskPlan(emptyList())

        val targets = Grammars.taskGraphTargets(sceneObjects)
        val system = prompt(Grammars.VOCABULARY, targets)
        val grammar = Grammars.taskGraph(sceneObjects)

        val raw = runCatching {
            // Empty history: this is a single-shot extraction, and carrying chat context
            // into it would let an earlier turn change how an instruction is read.
            repository.generate(
                history = listOf(ChatMessage(id = 0L, text = system, isUser = true)),
                userInput = instruction,
                grammar = grammar,
            ).toList().joinToString("")
        }.getOrElse { error ->
            Log.e(TAG, "generation failed", error)
            return TaskPlan(emptyList())
        }

        val plan = TaskPlan.parse(raw.trim(), targets)
        Log.i(TAG, "planned ${plan.steps.size} step(s) from ${raw.length} chars")
        return plan
    }
}

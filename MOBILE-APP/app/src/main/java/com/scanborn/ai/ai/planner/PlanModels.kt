package com.scanborn.ai.ai.planner

import com.scanborn.ai.ai.grammar.Grammars
import org.json.JSONObject

/**
 * The two things the on-device model is asked to produce, and the checks that decide whether
 * an answer is usable.
 *
 * The grammar in Grammars.kt already makes an invalid answer unrepresentable, so in practice
 * these validators should never reject anything. They exist anyway for two reasons: the
 * grammar is only applied when the native engine actually accepts one, and the same parsing
 * path is used for replies that arrive from the orchestrator, which has no grammar at all.
 * Validating both makes the phone's contract identical to the server's.
 *
 * Mirrors `sarvam/task_engine/graph.py` and `twin/profile.py`. The phone deliberately does
 * NOT carry the full ENVIRONMENTS table — keep-out distances and extra labels are the
 * server's to own, so a model on the phone can pick a space and its scalars but cannot
 * invent a safety rule. See `twin.profile.from_dict`.
 */

/** One step. Mirrors TaskNode; edges are implicit because these graphs are always linear. */
data class TaskStep(val action: String, val target: String)

data class TaskPlan(val steps: List<TaskStep>) {

    val isEmpty: Boolean get() = steps.isEmpty()

    /** The wire shape the orchestrator's /plan endpoint accepts. */
    fun toJson(): String {
        val nodes = steps.joinToString(",") { step ->
            """{"action":"${step.action}","target":"${step.target}"}"""
        }
        return """{"nodes":[$nodes]}"""
    }

    companion object {
        /**
         * Parse a model reply, dropping any step that is not usable.
         *
         * Dropping rather than failing is the same choice GroqPlanner.parse makes: three
         * good steps and one hallucination should still move the robot three steps.
         *
         * @param targets the legal target set for this scene, from Grammars.taskGraphTargets
         */
        fun parse(raw: String, targets: List<String>): TaskPlan {
            val legal = targets.toSet() + ""
            val steps = mutableListOf<TaskStep>()
            val nodes = runCatching { JSONObject(raw).optJSONArray("nodes") }.getOrNull()
                ?: return TaskPlan(emptyList())

            for (i in 0 until nodes.length()) {
                val node = nodes.optJSONObject(i) ?: continue
                val action = node.optString("action")
                val target = node.optString("target").lowercase()
                // Never trust a model with the vocabulary, or with this room's contents.
                if (action in Grammars.VOCABULARY && target in legal) {
                    steps.add(TaskStep(action, target))
                }
            }
            return TaskPlan(steps)
        }
    }
}

/**
 * Physical bounds on a profile, mirroring `schemas.PROFILE_BOUNDS`.
 *
 * A hallucinated 4-metre robot radius would inflate the navmesh until nothing is traversable
 * and surface three stages later as "no traversable cell", so the range check happens here
 * while the cause is still obvious.
 */
object ProfileBounds {
    val ROBOT_RADIUS = 0.05..1.50   // m, footprint used for costmap inflation
    val ROBOT_HEIGHT = 0.20..2.50   // m, top of the band a ground robot sweeps
    val MAX_SPEED = 0.05..2.00      // m/s, per-tick command ceiling
}

data class EnvironmentProfile(
    val kind: String,
    val robotRadius: Double,
    val robotHeight: Double,
    val maxSpeed: Double,
) {
    /** The wire shape, matching `schemas.environment_profile_grammar()` key order. */
    fun toJson(): String =
        """{"environment":"$kind","robot_radius":$robotRadius,""" +
            """"robot_height":$robotHeight,"max_speed":$maxSpeed}"""

    companion object {
        /** Reproduces `twin.profile.ENVIRONMENTS["generic"]` — the pre-profile constants. */
        val GENERIC = EnvironmentProfile("generic", 0.25, 1.00, 0.50)

        /**
         * Parse a model reply, or null if it is not usable.
         *
         * Returns null rather than a default so the caller decides what to fall back to.
         * Silently substituting `generic` here would make a rejected answer look like a
         * confident classification of an ordinary room.
         */
        fun parse(raw: String): EnvironmentProfile? {
            val doc = runCatching { JSONObject(raw) }.getOrNull() ?: return null

            val kind = doc.optString("environment")
            if (kind !in Grammars.ENVIRONMENT_KINDS) return null

            // optDouble returns NaN for a missing or non-numeric field, which fails every
            // range check below — so absent fields and garbage fields take the same path.
            val radius = doc.optDouble("robot_radius", Double.NaN)
            val height = doc.optDouble("robot_height", Double.NaN)
            val speed = doc.optDouble("max_speed", Double.NaN)

            if (radius !in ProfileBounds.ROBOT_RADIUS) return null
            if (height !in ProfileBounds.ROBOT_HEIGHT) return null
            if (speed !in ProfileBounds.MAX_SPEED) return null

            return EnvironmentProfile(kind, radius, height, speed)
        }
    }
}

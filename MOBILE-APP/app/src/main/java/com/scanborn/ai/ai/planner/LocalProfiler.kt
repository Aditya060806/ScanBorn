package com.scanborn.ai.ai.planner

import android.util.Log
import com.scanborn.ai.ai.grammar.Grammars
import com.scanborn.ai.ai.repository.AIRepository
import com.scanborn.ai.model.ChatMessage
import kotlinx.coroutines.flow.toList

/**
 * One segmented object as the summariser needs it.
 *
 * [bbox3d] is `[xmin, ymin, zmin, xmax, ymax, zmax]` in metres — the same order
 * `semantic/service/inference.py` emits, so a response from /segment maps straight onto this
 * without reordering.
 */
data class SceneObject(val label: String, val bbox3d: DoubleArray) {
    // DoubleArray gives array identity semantics for equals/hashCode, which is wrong for a
    // value type; generated explicitly so two objects with equal boxes compare equal.
    override fun equals(other: Any?): Boolean =
        other is SceneObject && label == other.label && bbox3d.contentEquals(other.bbox3d)

    override fun hashCode(): Int = 31 * label.hashCode() + bbox3d.contentHashCode()
}

/**
 * Which kind of space this is, decided on the phone.
 *
 * This is the first job in the pipeline where a small language model genuinely beats a rule:
 * turning "take the box to the table" into a task graph is something the existing keyword
 * grammar already does, whereas reasoning over a scene summary to pick a configuration is
 * not expressible as a regex.
 *
 * The model only ever chooses the kind and the three scalars. Keep-out distances and the
 * extra label vocabulary stay server-side in `twin/profile.py`, so a model here can tune how
 * much clearance a robot gets but cannot invent or delete a safety rule.
 */
class LocalProfiler(private val repository: AIRepository) {

    companion object {
        private const val TAG = "LocalProfiler"

        /** Structure, not furniture. Counting these would call every empty room crowded. */
        private val STRUCTURE = setOf("floor", "ceiling", "wall")

        /** Verbatim from `sarvam/task_engine/profile_planner.py:PROMPT`. */
        fun prompt(kinds: List<String>): String =
            "You classify indoor spaces for a mobile robot from a geometric summary.\n" +
                "Pick exactly one environment from: ${kinds.joinToString(", ")}.\n" +
                "Then choose the robot's footprint radius (m), sweep height (m) and top " +
                "speed (m/s) appropriate for that space. Larger spaces allow bigger, " +
                "faster robots; clinical and exhibition spaces need slower, more careful " +
                "ones.\n" +
                "Reply with JSON only: " +
                """{"environment": "...", "robot_radius": 0.0, """ +
                """"robot_height": 0.0, "max_speed": 0.0}"""

        /**
         * Two decimal places, formatted the way Python's `round(v, 2)` inside an f-string
         * prints: trailing zeros collapse, so 50.0 stays "50.0" and 12.34 stays "12.34".
         * Kotlin's Double.toString() already matches Python's repr across the range a room
         * produces.
         *
         * ponytail: Python's round() is banker's rounding and Math.round is half-up, so a
         * value landing exactly on .xx5 could differ in the last digit. Measured geometry
         * effectively never does, and the summary is prose for a model rather than a number
         * anyone compares — but that is the one case where these two would disagree.
         */
        internal fun metres(v: Double): String = (Math.round(v * 100.0) / 100.0).toString()

        /**
         * The scene as one short line — the model's entire input.
         *
         * Deliberately tiny: the phone pays for every prefill token, and a label histogram
         * plus three numbers is genuinely all the signal a bbox-level description carries.
         * Wording is verbatim from `profile_planner.summarize()`.
         */
        fun summarize(objects: List<SceneObject>): String {
            val labels = linkedMapOf<String, Int>()
            for (obj in objects) {
                val label = obj.label.lowercase()
                labels[label] = (labels[label] ?: 0) + 1
            }

            val boxes = objects.filter { it.bbox3d.size >= 6 }
            if (boxes.isEmpty()) {
                return line(0.0, 0.0, 0, labels)
            }

            // The floor decides the room's extent. Falling back to every box would let a
            // tall shelf against a wall inflate the footprint.
            val floors = boxes.filter { it.label.lowercase() == "floor" }
            val extent = floors.ifEmpty { boxes }

            val width = extent.maxOf { it.bbox3d[3] } - extent.minOf { it.bbox3d[0] }
            val depth = extent.maxOf { it.bbox3d[4] } - extent.minOf { it.bbox3d[1] }
            val ceiling = boxes.maxOf { it.bbox3d[5] } - boxes.minOf { it.bbox3d[2] }

            val furniture = labels.entries
                .filter { it.key !in STRUCTURE }
                .sumOf { it.value }

            return line(maxOf(0.0, width) * maxOf(0.0, depth), ceiling, furniture, labels)
        }

        private fun line(area: Double, ceiling: Double, objects: Int,
                         labels: Map<String, Int>): String {
            val counts = labels.entries.sortedBy { it.key }
                .joinToString(", ") { "${it.value} ${it.key}" }
                .ifEmpty { "no labelled objects" }
            return "Floor area ${metres(area)} m2, ceiling height ${metres(ceiling)} m, " +
                "$objects objects. Detected: $counts."
        }
    }

    /**
     * Infer the profile for a scanned scene.
     *
     * Falls back to [EnvironmentProfile.GENERIC] on any unusable answer — a missing model, a
     * parse failure, an out-of-range number. `generic` reproduces the pipeline's original
     * hardcoded constants, so a failed inference degrades to the behaviour that existed
     * before profiles rather than to a blocked navmesh.
     */
    suspend fun profile(objects: List<SceneObject>): EnvironmentProfile {
        val system = prompt(Grammars.ENVIRONMENT_KINDS)
        val summary = summarize(objects)

        val raw = runCatching {
            repository.generate(
                history = listOf(ChatMessage(text = system, isUser = true)),
                userInput = summary,
                grammar = Grammars.environmentProfile(),
            ).toList().joinToString("")
        }.getOrElse { error ->
            Log.e(TAG, "generation failed, using generic", error)
            return EnvironmentProfile.GENERIC
        }

        val parsed = EnvironmentProfile.parse(raw.trim())
        if (parsed == null) {
            Log.w(TAG, "unusable profile reply, using generic: ${raw.take(120)}")
            return EnvironmentProfile.GENERIC
        }
        Log.i(TAG, "profiled as ${parsed.kind} from: $summary")
        return parsed
    }
}

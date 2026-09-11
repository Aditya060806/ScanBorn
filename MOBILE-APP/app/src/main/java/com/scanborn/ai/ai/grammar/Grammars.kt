package com.scanborn.ai.ai.grammar

/**
 * GBNF grammars for the on-device model.
 *
 * These are the phone-side mirror of `sarvam/task_engine/schemas.py`. Both sides build the
 * same grammar from the same three lists, because the whole point of the grammar is that
 * the server and the phone agree on what a valid task graph is — a target the phone accepts
 * and the orchestrator rejects is worse than no constraint at all.
 *
 * `tests/test_grammar_parity.py` reads this file and fails if any of the three lists drifts
 * from the Python constants. That test is the reason these are written as plain lists rather
 * than fetched at runtime: drift becomes a red build instead of a bad plan on stage.
 *
 * Escaping note: GBNF string literals are double-quoted, so a literal JSON quote inside one
 * is a backslash-quote. `lit()` handles that; do not hand-write these strings.
 */
object Grammars {

    /** Mirrors graph.VOCABULARY — the only actions a robot adapter understands. */
    val VOCABULARY = listOf(
        "navigate_to", "pickup", "place", "inspect", "wait", "speak",
    )

    /** Mirrors semantic.service.inference.LABEL_ONTOLOGY. */
    val LABEL_ONTOLOGY = listOf(
        "wall", "floor", "ceiling", "door", "window",
        "chair", "table", "shelf", "cabinet",
        "robot", "person", "obstacle",
    )

    /** Mirrors schemas.ENVIRONMENT_KINDS. */
    val ENVIRONMENT_KINDS = listOf(
        "warehouse", "hospital", "factory", "museum", "home", "office", "generic",
    )

    /** A bare GBNF string literal: `{` becomes `"{"`. */
    private fun g(s: String) = "\"" + s + "\""

    /** A GBNF literal matching a quoted JSON string: `nodes` becomes `"\"nodes\""`. */
    private fun lit(v: String) = g("\\\"" + v + "\\\"")

    /** GBNF alternation over quoted JSON strings. */
    private fun alternates(values: List<String>) =
        values.joinToString(" | ") { lit(it) }

    /**
     * Legal `target` values: this scene's own labels first, then the ontology.
     *
     * Scene labels lead for the same reason the Python side orders them that way — a label
     * the room actually contains is a better guess than a generic ontology entry.
     */
    fun taskGraphTargets(objects: List<String> = emptyList()): List<String> {
        val seen = LinkedHashSet<String>()
        for (label in objects.map { it.lowercase() } + LABEL_ONTOLOGY) {
            if (label.isNotEmpty()) seen.add(label)
        }
        return seen.toList()
    }

    /**
     * A grammar that can only produce a valid task graph for this scene.
     *
     * The empty target is legal because `wait` and `speak` have none. Without it the
     * grammar could not express those two actions at all.
     */
    fun taskGraph(objects: List<String> = emptyList()): String {
        val targets = taskGraphTargets(objects) + ""
        return listOf(
            "root   ::= " + g("{") + " ws " + lit("nodes") + " ws " + g(":") + " ws " +
                g("[") + " ws (node (ws " + g(",") + " ws node)*)? ws " + g("]") +
                " ws " + g("}"),
            "node   ::= " + g("{") + " ws " + lit("action") + " ws " + g(":") +
                " ws action ws " + g(",") + " ws " + lit("target") + " ws " + g(":") +
                " ws target ws " + g("}"),
            "action ::= " + alternates(VOCABULARY),
            "target ::= " + alternates(targets),
            "ws     ::= [ \\t\\n]*",
            "",
        ).joinToString("\n")
    }

    /**
     * A grammar for the environment profile: one kind plus three bounded numbers.
     *
     * Key order is pinned by the grammar. A constrained decoder emits in grammar order, so
     * fixing the order removes a degree of freedom the model would otherwise spend, and
     * makes the output byte-comparable between runs.
     */
    fun environmentProfile(): String = listOf(
        "root ::= " + g("{") + " ws " +
            lit("environment") + " ws " + g(":") + " ws env ws " + g(",") + " ws " +
            lit("robot_radius") + " ws " + g(":") + " ws number ws " + g(",") + " ws " +
            lit("robot_height") + " ws " + g(":") + " ws number ws " + g(",") + " ws " +
            lit("max_speed") + " ws " + g(":") + " ws number ws " + g("}"),
        "env    ::= " + alternates(ENVIRONMENT_KINDS),
        "number ::= [0-9]+ (" + g(".") + " [0-9]+)?",
        "ws     ::= [ \\t\\n]*",
        "",
    ).joinToString("\n")
}

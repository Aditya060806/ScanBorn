package com.scanborn.ai.data.library

/** Only quoted Unicode word tokens reach SQLite's FTS query parser. */
object LibrarySearch {
    fun query(raw: String): String = Regex("[\\p{L}\\p{N}_]+")
        .findAll(raw).take(64).joinToString(" ") { "\"${it.value}\"*" }
}

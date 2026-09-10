package com.scanborn.ai.ai.streaming

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

object TokenStreamBuffer {
    // Strip leading whitespace from the first token.
    // Uses flow{} with its own local isFirst — safe for concurrent collections.
    fun clean(source: Flow<String>): Flow<String> = flow {
        var isFirst = true
        source.collect { token ->
            val out = if (isFirst) { isFirst = false; token.trimStart() } else token
            if (out.isNotEmpty()) emit(out)
        }
    }
}

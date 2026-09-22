package com.scanborn.ai.ai.prompts

import org.junit.Assert.assertTrue
import org.junit.Assert.assertFalse
import org.junit.Test

class PromptFormatterTest {
    @Test fun plannerOverrideUsesSystemRole() {
        val prompt = PromptFormatter.buildPrompt(emptyList(), "go home", "Return mission JSON")
        assertTrue(prompt.startsWith("<|im_start|>system\nReturn mission JSON<|im_end|>"))
        assertFalse(prompt.contains("You are ScanBorn"))
        assertTrue(prompt.contains("<|im_start|>user\ngo home<|im_end|>"))
    }

    @Test fun chatRetainsDefaultPersona() {
        assertTrue(PromptFormatter.buildPrompt(emptyList(), "hello").contains("You are ScanBorn"))
    }
}

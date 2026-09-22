package com.scanborn.ai.scan

import org.junit.Assert.*
import org.junit.Test

class UploadStatusTest {
    @Test fun `temporary HTTP refusals retain pending frames`() {
        for (code in listOf(408, 425, 429, 500, 502, 503, 504))
            assertFalse("$code must remain retryable", permanentlyRejectedChunk(code))
        for (code in listOf(400, 401, 403, 404, 409, 413, 422))
            assertTrue("$code rejects this submission", permanentlyRejectedChunk(code))
        assertFalse(permanentlyRejectedChunk(200))
    }
}

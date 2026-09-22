package com.scanborn.ai.scan

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class OrchestratorUrlTest {

    @Test
    fun `addresses are normalised to scheme, host and port`() {
        assertEquals("http://192.168.1.20:8000", OrchestratorUrl.normalise("192.168.1.20:8000"))
        assertEquals("http://192.168.1.20:8000", OrchestratorUrl.normalise(" http://192.168.1.20:8000/ "))
        assertEquals("https://laptop.local", OrchestratorUrl.normalise("HTTPS://laptop.local"))
    }

    @Test
    fun `anything that is not a bare orchestrator address is refused`() {
        assertNull(OrchestratorUrl.normalise(""))
        assertNull(OrchestratorUrl.normalise("ftp://192.168.1.20"))
        assertNull(OrchestratorUrl.normalise("http://192.168.1.20:8000/dashboard"))
        assertNull(OrchestratorUrl.normalise("http://192.168.1.20:8000?x=1"))
        assertNull(OrchestratorUrl.normalise("my laptop"))
    }
}

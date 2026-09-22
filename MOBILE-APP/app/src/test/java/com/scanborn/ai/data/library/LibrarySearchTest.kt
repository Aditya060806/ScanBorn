package com.scanborn.ai.data.library

import org.junit.Assert.assertEquals
import org.junit.Test

class LibrarySearchTest {
    @Test fun punctuationCannotBecomeFtsSyntax() {
        assertEquals("\"room\"* \"OR\"* \"robot\"*", LibrarySearch.query("room: OR (robot!)"))
        assertEquals("", LibrarySearch.query("\"*:()!?"))
        assertEquals("\"café\"* \"入口\"*", LibrarySearch.query("café 入口"))
    }
}

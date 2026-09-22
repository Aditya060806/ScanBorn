package com.scanborn.ai.spaces

import android.net.Uri

internal const val VIEWER_ORIGIN = "https://appassets.androidplatform.net"

internal fun acceptsViewerMessage(source: Uri, mainFrame: Boolean, message: String?): Boolean =
    mainFrame && source.toString() == VIEWER_ORIGIN && message != null && message.length <= 16384

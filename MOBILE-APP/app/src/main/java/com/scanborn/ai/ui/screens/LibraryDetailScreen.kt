package com.scanborn.ai.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.scanborn.ai.viewmodel.LibraryViewModel

@Composable
fun LibraryDetailScreen(id: Long?, vm: LibraryViewModel, onBack: () -> Unit) {
    val flow = remember(id, vm) { vm.entry(id ?: -1L) }
    val entry by flow.collectAsState(initial = null)
    Surface(modifier = Modifier.fillMaxSize()) {
        Column(Modifier.safeDrawingPadding().padding(20.dp).verticalScroll(rememberScrollState())) {
            TextButton(onClick = onBack) { Text("Back to Library") }
            val saved = entry
            if (saved == null) {
                Text("Entry unavailable. It may have been deleted.")
            } else {
                Text(saved.title, style = MaterialTheme.typography.headlineMedium)
                Text(saved.type.label, style = MaterialTheme.typography.labelLarge)
                if (saved.sourceInfo.isNotBlank()) Text(saved.sourceInfo)
                Spacer(Modifier.height(20.dp))
                SelectionContainer { Text(saved.content, style = MaterialTheme.typography.bodyLarge) }
            }
        }
    }
}

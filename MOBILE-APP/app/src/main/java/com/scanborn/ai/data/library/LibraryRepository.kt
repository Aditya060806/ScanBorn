package com.scanborn.ai.data.library

import android.content.Context
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.withContext

/**
 * LibraryRepository
 *
 * Single public API for all library operations.
 * ViewModels call save() after successful generation — fire-and-forget on IO dispatcher.
 * Never touches the AI pipeline.
 */
class LibraryRepository private constructor(context: Context) {

    private val dao = LibraryDatabase.getInstance(context).libraryDao()

    companion object {
        private const val TAG = "LibraryRepository"
        @Volatile private var INSTANCE: LibraryRepository? = null

        fun getInstance(context: Context): LibraryRepository =
            INSTANCE ?: synchronized(this) {
                INSTANCE ?: LibraryRepository(context.applicationContext).also { INSTANCE = it }
            }
    }

    // ── Write ─────────────────────────────────────────────────────────────────

    /**
     * Save an entry. Always call from Dispatchers.IO.
     * Auto-generates a title from the first line of content if none provided.
     */
    suspend fun save(
        type       : EntryType,
        content    : String,
        title      : String    = "",
        sourceInfo : String    = ""
    ): Long = withContext(Dispatchers.IO) {
        val resolvedTitle = title.ifBlank {
            content.lines().firstOrNull { it.isNotBlank() }
                ?.take(60)
                ?.trimEnd()
                ?: type.label
        }
        val entry = LibraryEntry(
            type       = type,
            title      = resolvedTitle,
            content    = content,
            sourceInfo = sourceInfo
        )
        val id = dao.insert(entry)
        Log.i(TAG, "Saved ${type.name} entry id=$id title='$resolvedTitle'")
        id
    }

    suspend fun delete(id: Long) = withContext(Dispatchers.IO) {
        dao.deleteById(id)
        Log.i(TAG, "Deleted entry id=$id")
    }

    // ── Read ──────────────────────────────────────────────────────────────────

    fun getAll(): Flow<List<LibraryEntry>>              = dao.getAll()
    fun getByType(type: EntryType): Flow<List<LibraryEntry>> = dao.getByType(type)

    /**
     * FTS search. The query string is automatically sanitised and wildcarded
     * so partial word matches work (e.g. "error" matches "errors").
     */
    fun getById(id: Long): Flow<LibraryEntry?> = dao.getById(id)

    fun search(raw: String, type: EntryType? = null): Flow<List<LibraryEntry>> {
        val safe = LibrarySearch.query(raw)
        return if (safe.isBlank()) {
            if (type == null) dao.getAll() else dao.getByType(type)
        } else dao.search(safe, type)
    }
}

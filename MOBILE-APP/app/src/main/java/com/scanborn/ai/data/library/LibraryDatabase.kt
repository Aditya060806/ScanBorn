package com.scanborn.ai.data.library

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import androidx.room.TypeConverter
import androidx.room.TypeConverters

@TypeConverters(EntryTypeConverter::class)
@Database(
    entities  = [LibraryEntry::class, LibraryEntryFts::class],
    version   = 2,
    exportSchema = false
)
abstract class LibraryDatabase : RoomDatabase() {

    abstract fun libraryDao(): LibraryDao

    companion object {
        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("INSERT INTO library_entries_fts(library_entries_fts) VALUES('rebuild')")
            }
        }

        @Volatile private var INSTANCE: LibraryDatabase? = null

        fun getInstance(context: Context): LibraryDatabase =
            INSTANCE ?: synchronized(this) {
                INSTANCE ?: Room.databaseBuilder(
                    context.applicationContext,
                    LibraryDatabase::class.java,
                    "scanborn_library.db"
                )
                .addMigrations(MIGRATION_1_2)
                .build()
                .also { INSTANCE = it }
            }
    }
}

class EntryTypeConverter {
    @TypeConverter fun fromEntryType(type: EntryType): String = type.name
    @TypeConverter fun toEntryType(name: String): EntryType   = EntryType.valueOf(name)
}

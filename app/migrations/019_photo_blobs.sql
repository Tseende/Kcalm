-- Копии фото для облачного режима: диск Render временный, файлы восстанавливаются из БД при старте.
CREATE TABLE IF NOT EXISTS photo_blobs(
    path TEXT PRIMARY KEY,
    data BLOB NOT NULL);

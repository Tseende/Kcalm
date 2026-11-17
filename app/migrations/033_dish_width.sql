-- Container length uses the existing primary dimension; width is independent.
ALTER TABLE dishes ADD COLUMN width_cm REAL;

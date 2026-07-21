import sqlite3

def migrate():
    conn = sqlite3.connect('phoenix_coach.db')
    cursor = conn.cursor()

    # Columns to add to athletes
    athlete_cols = [
        ("vo2_max", "FLOAT"),
        ("stamina_level", "FLOAT")
    ]
    
    # Columns to add to activities
    activity_cols = [
        ("training_load", "FLOAT"),
        ("step_count", "INTEGER"),
        ("sets", "INTEGER"),
        ("cadence", "INTEGER"),
        ("sport_code", "INTEGER"),
        ("sub_mode", "INTEGER")
    ]
    
    # Columns to add to recovery_snapshots
    snapshot_cols = [
        ("hrv_baseline", "FLOAT"),
        ("hrv_sd", "FLOAT"),
        ("vo2_max", "FLOAT"),
        ("ati", "FLOAT"),
        ("cti", "FLOAT"),
        ("tib", "FLOAT"),
        ("fatigue_pct", "FLOAT"),
        ("fatigue_state", "INTEGER"),
        ("load_ratio", "FLOAT"),
        ("load_ratio_state", "INTEGER"),
        ("t7d_load", "FLOAT"),
        ("t28d_load", "FLOAT"),
        ("recommend_tl_max", "FLOAT"),
        ("recommend_tl_min", "FLOAT"),
        ("lthr", "INTEGER"),
        ("ltsp", "INTEGER"),
        ("performance_index", "FLOAT"),
        ("performance_score", "INTEGER")
    ]

    def add_cols(table, cols):
        for col_name, col_type in cols:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                print(f"Added {col_name} to {table}")
            except sqlite3.OperationalError:
                print(f"Column {col_name} already exists in {table}")

    add_cols("athletes", athlete_cols)
    add_cols("activities", activity_cols)
    add_cols("recovery_snapshots", snapshot_cols)

    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()

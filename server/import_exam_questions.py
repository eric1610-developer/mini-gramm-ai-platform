import csv
from pathlib import Path
from sqlalchemy import select
from app.db import SessionLocal
from app.models import ExamQuestion

def import_csv(exam_key: str, path: Path):
    db = SessionLocal()
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            n_new = 0
            for row in r:
                qcode = (row.get("id") or "").strip()
                if not qcode:
                    continue

                exists = db.scalar(select(ExamQuestion).where(
                    ExamQuestion.exam_key == exam_key,
                    ExamQuestion.qcode == qcode
                ))
                if exists:
                    continue

                q = ExamQuestion(
                    exam_key=exam_key,
                    qcode=qcode,
                    topic=(row.get("topic") or "").strip() or "Общее",
                    question=(row.get("question") or "").strip(),
                    qtype=(row.get("type") or "single").strip(),
                    options=(row.get("options") or "").strip(),
                    correct=(row.get("correct") or "").strip(),
                    explanation=(row.get("explanation") or "").strip(),
                    media_path=(row.get("media_path") or "").strip(),
                    is_active=1,
                )
                db.add(q)
                n_new += 1

            db.commit()
            print(f"OK: imported {n_new} new questions from {path.name} to {exam_key}")
    finally:
        db.close()

def main():
    base = Path(__file__).resolve().parent
    import_csv("GPM", base / "mini_gramm_gpm_tests.csv")
    import_csv("OTTB", base / "mini_gramm_ottb_tests.csv")

if __name__ == "__main__":
    main()
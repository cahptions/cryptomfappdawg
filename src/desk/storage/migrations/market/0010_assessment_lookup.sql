-- The Scanner shows each candidate's latest security assessment (verdict, reasons, round-trip cost, holders).
CREATE INDEX security_assessments_candidate ON security_assessments(candidate_id, id);

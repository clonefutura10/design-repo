export type JobStatus = "pending" | "processing" | "completed" | "failed";

export interface AnnotationStats {
  total_pages: number;
  total_fields_extracted: number;
  unique_forms: number;
  fields_after_noise_filter: number;
  noise_removed: number;
  resolved_count: number;
  unresolved_count: number;
  resolution_rate: number;
  annotations_written: number;
  pages_annotated: number;
  not_submitted_count: number;
  duplicates_skipped: number;
  skipped_no_position: number;
  tier0_regex: number;
  tier0_standards: number;
  tier0_az_spec: number;
}

export interface AnnotationResponse {
  job_id: string;
  status: JobStatus;
  stats: AnnotationStats;
  message: string;
  filename: string;
}

export interface FieldMapping {
  form_code: string;
  field_label: string;
  annotation: string;
  additional_annotations: string[];
  sdtm_domain: string | null;
  sdtm_variable: string | null;
  codelist_code: string | null;
  is_supplemental: boolean;
  is_not_submitted: boolean;
  confidence: number;
  tier: string;
}

export interface AnnotationDetail {
  job_id: string;
  total_mappings: number;
  resolved_count: number;
  unresolved_count: number;
  resolved: FieldMapping[];
  unresolved: FieldMapping[];
}

export interface JobSummary {
  job_id: string;
  status: JobStatus;
  filename: string;
  annotations_written: number;
  resolution_rate: number;
}

export interface AnnotationOverride {
  form_code: string;
  field_label: string;
  annotations: string[];
}

export interface EditResponse {
  job_id: string;
  message: string;
  changes_applied: number;
  stats: AnnotationStats;
}

-- Import melalui phpMyAdmin atau: mysql -u root -p < mysql/001_initial.sql
create database if not exists iphone_diagnosis
  character set utf8mb4 collate utf8mb4_unicode_ci;
use iphone_diagnosis;

create table if not exists admin_users (
  id bigint unsigned auto_increment primary key,
  username varchar(120) not null unique,
  password_hash varchar(255) not null,
  role enum('admin','expert') not null default 'expert',
  is_active boolean not null default true,
  failed_login_count int not null default 0,
  locked_until datetime null,
  last_login_at datetime null,
  created_at datetime not null default current_timestamp,
  updated_at datetime not null default current_timestamp on update current_timestamp
) engine=InnoDB;

create table if not exists diagnosis_history (
  id bigint unsigned auto_increment primary key,
  case_number varchar(40) not null unique,
  customer_name varchar(120) not null,
  phone_type varchar(120) not null,
  complaint text not null,
  features_json longtext not null,
  features_confirmed boolean not null default false,
  top_diagnosis varchar(200) not null,
  top_probability decimal(10,8) null,
  all_diagnoses_json longtext null,
  ds_metrics_json longtext null,
  is_software boolean not null default false,
  extractor_provider varchar(80) null,
  extractor_model varchar(120) null,
  duration_ms int null,
  model_version varchar(120) not null default 'legacy',
  created_at datetime not null default current_timestamp,
  index idx_diagnosis_created(created_at),
  index idx_diagnosis_class(top_diagnosis),
  index idx_diagnosis_model(model_version)
) engine=InnoDB;

create table if not exists case_verifications (
  id bigint unsigned auto_increment primary key,
  diagnosis_id bigint unsigned not null unique,
  actual_diagnosis varchar(200) null,
  verification_status enum('pending','verified','rejected','needs_revision') not null default 'pending',
  expert_notes text null,
  verified_by bigint unsigned null,
  verified_at datetime null,
  created_at datetime not null default current_timestamp,
  updated_at datetime not null default current_timestamp on update current_timestamp,
  constraint fk_verification_diagnosis foreign key (diagnosis_id) references diagnosis_history(id) on delete cascade,
  constraint fk_verification_admin foreign key (verified_by) references admin_users(id) on delete set null,
  index idx_verification_status(verification_status)
) engine=InnoDB;

create table if not exists repair_prices (
  id bigint unsigned auto_increment primary key,
  damage_class varchar(200) not null,
  phone_model_pattern varchar(120) not null default '*',
  service_name varchar(200) not null,
  minimum_price decimal(14,2) not null,
  maximum_price decimal(14,2) not null,
  currency char(3) not null default 'IDR',
  notes text null,
  is_active boolean not null default true,
  effective_from datetime not null default current_timestamp,
  effective_until datetime null,
  updated_by bigint unsigned null,
  created_at datetime not null default current_timestamp,
  updated_at datetime not null default current_timestamp on update current_timestamp,
  constraint fk_price_admin foreign key (updated_by) references admin_users(id) on delete set null,
  constraint chk_price_range check (minimum_price >= 0 and maximum_price >= minimum_price),
  index idx_price_lookup(damage_class, is_active, effective_from)
) engine=InnoDB;

create table if not exists training_datasets (
  id bigint unsigned auto_increment primary key,
  version varchar(120) not null unique,
  source varchar(80) not null,
  row_count int not null,
  file_path varchar(500) not null,
  sha256 char(64) not null,
  parent_version varchar(120) null,
  status enum('draft','validated','archived') not null default 'draft',
  created_by bigint unsigned null,
  created_at datetime not null default current_timestamp,
  constraint fk_dataset_admin foreign key (created_by) references admin_users(id) on delete set null
) engine=InnoDB;

create table if not exists training_jobs (
  id bigint unsigned auto_increment primary key,
  job_type enum('preprocessing','pso','evaluation') not null,
  dataset_version varchar(120) null,
  status enum('queued','running','succeeded','failed','cancelled') not null default 'queued',
  progress int not null default 0,
  parameters_json longtext null,
  log_path varchar(500) null,
  artifact_path varchar(500) null,
  metrics_json longtext null,
  started_by bigint unsigned null,
  started_at datetime null,
  finished_at datetime null,
  error_message text null,
  created_at datetime not null default current_timestamp,
  constraint fk_job_admin foreign key (started_by) references admin_users(id) on delete set null,
  index idx_job_status(status),
  index idx_job_created(created_at)
) engine=InnoDB;

create table if not exists model_versions (
  id bigint unsigned auto_increment primary key,
  version varchar(120) not null unique,
  knowledge_base_path varchar(500) not null,
  dataset_hash char(64) null,
  metrics_json longtext null,
  metadata_json longtext null,
  is_active boolean not null default false,
  approved_by bigint unsigned null,
  approved_at datetime null,
  created_at datetime not null default current_timestamp,
  constraint fk_model_admin foreign key (approved_by) references admin_users(id) on delete set null,
  index idx_model_active(is_active)
) engine=InnoDB;

create table if not exists audit_logs (
  id bigint unsigned auto_increment primary key,
  admin_id bigint unsigned null,
  action varchar(120) not null,
  entity_type varchar(80) not null,
  entity_id varchar(120) null,
  before_json longtext null,
  after_json longtext null,
  ip_hash char(64) null,
  created_at datetime not null default current_timestamp,
  constraint fk_audit_admin foreign key (admin_id) references admin_users(id) on delete set null,
  index idx_audit_created(created_at),
  index idx_audit_entity(entity_type, entity_id)
) engine=InnoDB;

create table if not exists api_rate_limits (
  identifier char(64) not null,
  window_start datetime not null,
  request_count int not null default 1,
  primary key(identifier, window_start)
) engine=InnoDB;

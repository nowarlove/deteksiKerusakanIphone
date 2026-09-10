-- Ganti password sebelum menjalankan melalui phpMyAdmin sebagai root.
create user if not exists 'iphone_app'@'localhost' identified by 'admin123';
grant select, insert, update, delete on iphone_diagnosis.* to 'iphone_app'@'localhost';
flush privileges;
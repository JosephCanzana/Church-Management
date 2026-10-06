.PHONY: tailwind-watch tailwind-build

tailwind-watch:
	./theme/tailwindcss -i ./static/css/input.css -o ./static/css/output.css --watch

tailwind-build:
	./theme/tailwindcss -i ./static/css/input.css -o ./static/css/output.css --minify
reset-dev:
	docker compose down -v
	docker compose up --build -d
	docker compose exec web python manage.py migrate
	docker compose exec web python manage.py seed_superadmin

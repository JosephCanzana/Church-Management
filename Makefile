.PHONY: tailwind-watch tailwind-build

tailwind-watch:
	./theme/tailwindcss -i ./static/css/input.css -o ./static/css/output.css --watch

tailwind-build:
	./theme/tailwindcss -i ./static/css/input.css -o ./static/css/output.css --minify
FROM ruby:3.3.10-bookworm

WORKDIR /preview
COPY Gemfile* ./
RUN bundle install

ENV BUNDLE_GEMFILE=/preview/Gemfile
EXPOSE 4000 35729
CMD ["bundle", "exec", "jekyll", "serve", "--source", "/site", "--destination", "/tmp/site", "--host", "0.0.0.0", "--livereload", "--force_polling"]

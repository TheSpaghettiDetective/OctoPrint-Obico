var gulp = require('gulp');
var less = require('gulp-less');
var sourcemaps = require('gulp-sourcemaps');

gulp.task('less', function() {
    return gulp.src('octoprint_obico/static/less/**/*.less') // Gets all files ending with .scss in app/scss and children dirs
        .pipe(sourcemaps.init())
        .pipe(less())
        .pipe(sourcemaps.write())
        .pipe(gulp.dest('octoprint_obico/static/css'));
});

gulp.task('watch', function() {
    gulp.watch('octoprint_obico/static/less/**/*.less', gulp.series('less'));
});

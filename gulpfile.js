var gulp = require('gulp');
var less = require('gulp-less');
var sourcemaps = require('gulp-sourcemaps');

gulp.task('less', function() {
    return gulp.src('octoprint_thespaghettidetective/static/less/**/*.less') // Gets all files ending with .scss in app/scss and children dirs
        .pipe(sourcemaps.init())
        .pipe(less())
        .pipe(sourcemaps.write())
        .pipe(gulp.dest('octoprint_thespaghettidetective/static/css'));
});

gulp.task('watch', function() {
    gulp.watch('octoprint_thespaghettidetective/static/less/**/*.less', gulp.series('less'));
});
